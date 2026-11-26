import logging
from uuid import uuid4

from fastapi import HTTPException
from redis import Redis
from redis.exceptions import RedisError
from sqlalchemy import func, select

from .audit import record
from .cohorts import count_cohorts, load_wave
from .constants import MIN_COHORT, MIN_TOTAL, ReportState, Role, TrialState, WaveState
from .factor_math import UnfitObservation, estimate_trial_loading, identify_factors
from .form_models import FormItem, Item
from .report_models import FactorReport
from .settings import QUEUE_KEY, REDIS_URL
from .survey_models import Answer, Survey

logger = logging.getLogger("pool.reports")


def get_report(db, report_id, *, lock=False):
    query = select(FactorReport).where(FactorReport.id == report_id)
    row = db.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise HTTPException(404, "找不到因子核校单。")
    return row


def _trial_counts(db, wave_id, item_id):
    """Valid (non-blank) answer sheets for a trial item per cohort and overall."""
    tally = (select(Survey.cohort_id, func.count(func.distinct(Survey.id)))
             .join(Answer, Answer.survey_id == Survey.id)
             .where(Survey.wave_id == wave_id, Answer.item_id == item_id)
             .group_by(Survey.cohort_id))
    per_cohort = dict(db.execute(tally).all())
    total = db.scalar(select(func.count(func.distinct(Survey.id)))
                      .join(Answer, Answer.survey_id == Survey.id)
                      .where(Survey.wave_id == wave_id, Answer.item_id == item_id))
    return per_cohort, total or 0


def _build_trials(db, wave, trial_sections, surveys, factor_scores):
    """Estimate loadings for every试填题 against the fixed formal factor scores."""
    cohort_rows = count_cohorts(db, wave.id)
    results = []
    for section in trial_sections:
        item = db.get(Item, section.item_id)
        per_cohort, total = _trial_counts(db, wave.id, item.id)
        short = [cohort.code for cohort, _ in cohort_rows
                 if per_cohort.get(cohort.id, 0) < MIN_COHORT]
        if short or total < MIN_TOTAL:
            reasons = [f"教练组 {'、'.join(short)} 该题有效作答不足 {MIN_COHORT} 份"] if short else []
            if total < MIN_TOTAL:
                reasons.append(f"整批该题有效作答不足 {MIN_TOTAL} 份")
            results.append({"item_id": item.id, "code": item.code, "state": TrialState.SKIPPED,
                            "reason": "；".join(reasons), "loadings": None, "primary": None,
                            "strong": False, "count": total})
            continue
        eligible_ids = {survey_id for survey_id, in db.execute(
            select(Survey.id).join(Answer, Answer.survey_id == Survey.id)
            .where(Survey.wave_id == wave.id, Answer.item_id == item.id)).all()}
        estimate = estimate_trial_loading(item, surveys, factor_scores, eligible_ids)
        if estimate is None:
            results.append({"item_id": item.id, "code": item.code, "state": TrialState.SKIPPED,
                            "reason": "该题作答没有组内变化，无法估计载荷。", "loadings": None,
                            "primary": None, "strong": False, "count": total})
        else:
            results.append({"item_id": item.id, "code": item.code, "state": TrialState.ELIGIBLE,
                            "reason": None, **estimate})
    return results


def display_report(row, *, viewer_role=None):
    trials = row.trials
    if trials is not None and viewer_role == Role.VIEWER:
        # 受门槛控制的回收数字对查阅岗脱敏：只留状态，不给数量。
        trials = [{**trial, "count": None, "reason": None} for trial in trials]
    return {"id": row.id, "wave_id": row.wave_id, "code": row.code, "state": row.state,
            "reviewed_by": row.reviewed_by, "review_note": row.review_note,
            "findings": row.findings, "trials": trials}


def queue_report(db, wave_id, actor):
    wave = load_wave(db, wave_id, lock=True)
    if wave.state != WaveState.COLLECTING:
        raise HTTPException(409, "本轮已冻结，不能重复提报。")
    counts = count_cohorts(db, wave.id)
    missing = [item.code for item, size in counts if size < MIN_COHORT]
    if missing:
        raise HTTPException(409, f"教练组 {'、'.join(missing)} 未达到 {MIN_COHORT} 份匿名门槛，禁止合并。")
    if sum(size for _, size in counts) < MIN_TOTAL:
        raise HTTPException(409, f"合并建模需要至少 {MIN_TOTAL} 份完整观察。")
    report = FactorReport(wave_id=wave.id, code="FA-" + uuid4().hex[:10].upper(),
                          state=ReportState.QUEUED, requested_by=actor)
    wave.state = WaveState.ANALYZING
    db.add(report)
    record(db, actor, "freeze_for_fit", wave.code)
    db.commit()
    try:
        Redis.from_url(REDIS_URL, socket_timeout=3, socket_connect_timeout=2).lpush(QUEUE_KEY, report.id)
    except RedisError:
        logger.warning("Database request retained: %s", report.code)
    return report


def do_pending(db):
    pending = db.scalar(select(FactorReport).where(FactorReport.state == ReportState.QUEUED)
                        .order_by(FactorReport.id).limit(1).with_for_update(skip_locked=True))
    if pending is None:
        return False
    wave = load_wave(db, pending.wave_id, lock=True)
    sections = (db.scalars(select(FormItem).where(FormItem.form_id == wave.form_id)
                           .order_by(FormItem.position)).all())
    formal_sections = [section for section in sections if not section.is_trial]
    trial_sections = [section for section in sections if section.is_trial]
    formal_items = [db.get(Item, section.item_id) for section in formal_sections]
    surveys = db.scalars(select(Survey).where(Survey.wave_id == wave.id).order_by(Survey.id)).all()
    try:
        # 正式因子只用正式题拟合；试题不进入这一步。
        findings, factor_scores = identify_factors(formal_items, surveys)
        # 试题载荷在同一事务内、基于该批固定因子分数估计，中断恢复仍读同一批答卷。
        trials = _build_trials(db, wave, trial_sections, surveys, factor_scores)
    except UnfitObservation as error:
        pending.state, pending.review_note, wave.state = ReportState.FAILED, str(error), WaveState.COLLECTING
    else:
        pending.findings, pending.trials = findings, trials
        pending.state, wave.state = ReportState.READY, WaveState.REVIEW
    record(db, "worker", "factor_" + pending.state, pending.code)
    db.commit()
    return True


def approve(db, report_id, note, actor):
    report = get_report(db, report_id, lock=True)
    if report.state != ReportState.READY:
        raise HTTPException(409, "须先完成待核校的结果。")
    if actor == report.requested_by:
        raise HTTPException(409, "请交由不同岗位核校。")
    report.state, report.review_note, report.reviewed_by = ReportState.APPROVED, note, actor
    record(db, actor, "approve_factor_review", report.code)
    db.commit()
    return report

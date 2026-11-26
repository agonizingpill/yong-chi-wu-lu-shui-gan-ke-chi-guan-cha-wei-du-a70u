from fastapi import HTTPException
from sqlalchemy import select

from .audit import record
from .cohort_models import Cohort, Wave
from .cohorts import load_wave
from .constants import FormState, ReportState, TrialState, WaveState
from .factor_math import MIN_PRIMARY_LOADING
from .form_models import EditionRegister, FormItem, Item, ObservationForm
from .report_control import get_report


def available_items(db):
    return {item.id: item for item in db.scalars(select(Item))}


def describe_form(form, items):
    return {"id": form.id, "edition": form.edition, "title": form.title, "state": form.state,
            "source_report_id": form.source_report_id, "signed_by": form.signed_by,
            "items": [{"id": section.item_id, "code": items[section.item_id].code,
                       "prompt": items[section.item_id].prompt, "reversed": items[section.item_id].reversed,
                       "pinned": items[section.item_id].pinned, "position": section.position,
                       "factor": section.factor, "is_trial": section.is_trial}
                      for section in form.sections]}


def _trial_index(report):
    """Pre-signing trial snapshot stored on the report; nothing else is trusted."""
    return {trial["item_id"]: trial for trial in (report.trials or [])}


def revise(db, data, actor):
    edition = db.scalar(select(EditionRegister).where(EditionRegister.id == 1).with_for_update())
    if edition is None:
        raise HTTPException(409, "观察表版次尚未建立。")
    report = get_report(db, data.report_id, lock=True)
    wave = load_wave(db, report.wave_id, lock=True)
    if report.state != ReportState.APPROVED or wave.state != WaveState.REVIEW or report.findings is None:
        raise HTTPException(409, "新表需要已核校且未使用过的因子报告。")
    if edition.current_form_id != wave.form_id:
        raise HTTPException(409, "本轮基于旧版，当前版次已更新，请重新组织观察。")
    old = db.get(ObservationForm, wave.form_id)
    items = available_items(db)
    positions = {entry.item_id: entry for entry in old.sections}
    chosen = set(data.item_ids)
    if not chosen.issubset(positions):
        raise HTTPException(409, "不得从本轮观察表以外添加未经核验的条目。")
    pinned = {entry_id for entry_id, entry in positions.items() if items[entry_id].pinned}
    if not chosen.issuperset(pinned):
        raise HTTPException(409, "须保留固定的开场登记条目。")

    trial_rows = _trial_index(report)
    formal_loadings = {row["item_id"]: row for row in report.findings["items"]}
    promoted = {item_id for item_id in chosen if positions[item_id].is_trial}
    not_ready = []
    for item_id in promoted:
        trial = trial_rows.get(item_id)
        # 只认签发前已存入报告、且达到现有载荷门槛的试题。
        if trial is None or trial.get("state") != TrialState.ELIGIBLE or not trial.get("strong"):
            not_ready.append(items[item_id].code)
    if not_ready:
        raise HTTPException(
            409, f"试题 {'、'.join(not_ready)} 尚未在签发前取得达到 {MIN_PRIMARY_LOADING} 载荷门槛的结果，不能选为正式题。")

    # 因子覆盖由全部已选正式题共同满足：老正式题看正式载荷，晋升题看试题载荷。
    factors = set()
    for item_id in chosen:
        if item_id in formal_loadings and formal_loadings[item_id]["strong"]:
            factors.add(formal_loadings[item_id]["primary"])
        elif item_id in trial_rows and trial_rows[item_id].get("strong"):
            factors.add(trial_rows[item_id]["primary"])
    if factors != {1, 2}:
        raise HTTPException(409, "下一轮观察表须各保留一个主要载荷达到阈值的条目。")

    new_trial_specs = data.trial_items or []
    existing_codes = {item.code for item in items.values()}
    new_trial_codes = {spec.code for spec in new_trial_specs}
    if len(new_trial_codes) != len(new_trial_specs):
        raise HTTPException(409, "新增试题编号不能重复。")
    if new_trial_codes & existing_codes:
        raise HTTPException(409, "试题编号已存在，请换用新编号。")
    new_trial_items = []
    for spec in new_trial_specs:
        trial_item = Item(code=spec.code, prompt=spec.prompt, domain="trial",
                          reversed=spec.reversed, pinned=False)
        db.add(trial_item)
        new_trial_items.append(trial_item)
    db.flush()

    sections, evidence = [], []
    position = 1
    for item_id in data.item_ids:
        was_trial = positions[item_id].is_trial
        factor = (trial_rows[item_id]["primary"] if was_trial
                  else formal_loadings[item_id]["primary"])
        sections.append(FormItem(item_id=item_id, position=position, factor=factor, is_trial=False))
        evidence.append({"item_id": item_id, "code": items[item_id].code, "is_trial": False,
                        "promoted_from_trial": was_trial,
                        "factor": factor,
                        "basis": (trial_rows[item_id] if was_trial else formal_loadings[item_id])})
        position += 1
    # 未选中的试题随新表继续试填，保留其题目身份。
    carried = [entry for item_id, entry in positions.items()
               if entry.is_trial and item_id not in chosen]
    for entry in carried:
        sections.append(FormItem(item_id=entry.item_id, position=position, factor=None, is_trial=True))
        evidence.append({"item_id": entry.item_id, "code": items[entry.item_id].code,
                         "is_trial": True, "carried": True})
        position += 1
    for trial_item in new_trial_items:
        sections.append(FormItem(item_id=trial_item.id, position=position, factor=None, is_trial=True))
        evidence.append({"item_id": trial_item.id, "code": trial_item.code,
                         "is_trial": True, "new_trial": True})
        position += 1

    new_form = ObservationForm(
        edition=data.edition, title=data.title, state=FormState.ACTIVE, source_report_id=report.id,
        signed_by=actor, sections=sections)
    db.add(new_form)
    db.flush()
    next_wave = Wave(code=data.next_code, title=data.next_title, form_id=new_form.id, state=WaveState.COLLECTING)
    db.add(next_wave)
    db.flush()
    for old_cohort in db.scalars(select(Cohort).where(Cohort.wave_id == wave.id)):
        db.add(Cohort(wave_id=next_wave.id, code=old_cohort.code, label=old_cohort.label, planned=old_cohort.planned))
    wave.state, wave.next_wave_id = WaveState.SEALED, next_wave.id
    old.state, edition.current_form_id = FormState.ARCHIVED, new_form.id
    # 签发同时落库：只认此刻报告中已存下的估计结果。
    record(db, actor, "publish_form_revision", f"{data.edition}/{data.next_code}")
    record(db, actor, "signed_trial_evidence", f"{data.edition}@{report.code}")
    new_form.trial_evidence = evidence
    db.commit()
    return {"form": describe_form(new_form, available_items(db)),
            "new_wave_id": next_wave.id}


def add_trial_item(db, form_id, data, actor):
    form = db.get(ObservationForm, form_id)
    if form is None:
        raise HTTPException(404, "找不到观察表。")
    if form.state != FormState.ACTIVE:
        raise HTTPException(409, "只能向现行观察表追加试题。")
    if db.scalar(select(Item).where(Item.code == data.code)) is not None:
        raise HTTPException(409, "试题编号已存在，请换用新编号。")
    item = Item(code=data.code, prompt=data.prompt, domain="trial",
                reversed=data.reversed, pinned=False)
    db.add(item)
    db.flush()
    position = (max((section.position for section in form.sections), default=0)) + 1
    db.add(FormItem(form_id=form.id, item_id=item.id, position=position, factor=None, is_trial=True))
    record(db, actor, "append_trial_item", f"{form.edition}/{data.code}")
    db.commit()
    return describe_form(db.get(ObservationForm, form.id), available_items(db))

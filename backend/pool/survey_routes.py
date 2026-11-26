from hashlib import sha256

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, conint
from sqlalchemy import select

from .audit import record
from .auth import Db, Technician
from .cohort_models import Cohort
from .cohorts import load_wave
from .constants import WaveState
from .form_models import FormItem
from .survey_models import Answer, Survey

router = APIRouter(prefix="/surveys", tags=["匿名观察"])

Scores = conint(strict=True, ge=1, le=5)


class AnswerSheet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    wave_id: int = Field(gt=0)
    cohort_id: int = Field(gt=0)
    ticket: str = Field(pattern=r"^[a-fA-F0-9]{16,32}$")
    consent: StrictBool
    # 正式题必答；试题可留空（缺键即视为该题未填）。上限覆盖正式题与逐轮续填的试题。
    answers: dict[int, Scores] = Field(min_length=2, max_length=64)


@router.post("", status_code=201)
def register(sheet: AnswerSheet, db: Db, actor: Technician):
    if not sheet.consent:
        raise HTTPException(409, "只有同意匿名汇总的问卷可以提交。")
    wave = load_wave(db, sheet.wave_id, lock=True)
    if wave.state != WaveState.COLLECTING:
        raise HTTPException(409, "当前批次已冻结，只能在下一轮表单收集。")
    cohort = db.get(Cohort, sheet.cohort_id)
    if cohort is None or cohort.wave_id != wave.id:
        raise HTTPException(409, "教练组不属于该轮观察表。")
    sections = db.scalars(select(FormItem).where(FormItem.form_id == wave.form_id)).all()
    formal_ids = {section.item_id for section in sections if not section.is_trial}
    trial_ids = {section.item_id for section in sections if section.is_trial}
    given = set(sheet.answers)
    unknown = given - formal_ids - trial_ids
    if unknown:
        raise HTTPException(422, "答案包含本轮观察表以外的条目。")
    missing_formal = formal_ids - given
    if missing_formal:
        raise HTTPException(422, "正式量表题必须全部作答，试题才可留空。")
    fingerprint = sha256(sheet.ticket.lower().encode()).hexdigest()
    row = Survey(wave_id=wave.id, cohort_id=cohort.id, ticket_hash=fingerprint,
                 marks=[Answer(item_id=i, raw_value=value) for i, value in sorted(sheet.answers.items())])
    db.add(row)
    record(db, actor.username, "receive_consent", wave.code)
    db.commit()
    return {"accepted": True, "wave_code": wave.code}

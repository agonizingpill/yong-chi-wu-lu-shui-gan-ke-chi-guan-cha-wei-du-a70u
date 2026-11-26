from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator
from sqlalchemy import select

from .auth import Current, Db, Supervisor
from .form_control import add_trial_item, available_items, describe_form, revise
from .form_models import ObservationForm

router = APIRouter(prefix="/forms", tags=["观察表版次"])


class ReleaseRevision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: int = Field(gt=0)
    edition: str = Field(pattern=r"^[A-Z0-9-]{4,20}$")
    title: str = Field(min_length=6, max_length=80)
    next_code: str = Field(pattern=r"^[A-Z0-9-]{4,18}$")
    next_title: str = Field(min_length=6, max_length=80)
    item_ids: list[int] = Field(min_length=4, max_length=12)
    trial_items: list["TrialItemSpec"] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def no_duplicates(self):
        if len(set(self.item_ids)) != len(self.item_ids):
            raise ValueError("同一条目不能重复放入新版表单。")
        return self


class TrialItemSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Z]{2}-[0-9]{2}$")
    prompt: str = Field(min_length=6, max_length=120)
    reversed: StrictBool = False


class TrialAppend(TrialItemSpec):
    form_id: int = Field(gt=0)


ReleaseRevision.model_rebuild()


@router.get("")
def editions(db: Db, _actor: Current):
    items = available_items(db)
    return [describe_form(form, items) for form in db.scalars(select(ObservationForm).order_by(ObservationForm.id.desc()))]


@router.post("/publish", status_code=201)
def publish_form(data: ReleaseRevision, db: Db, actor: Supervisor):
    return revise(db, data, actor.username)


@router.post("/trial", status_code=201)
def append_trial(data: TrialAppend, db: Db, actor: Supervisor):
    return add_trial_item(db, data.form_id, data, actor.username)

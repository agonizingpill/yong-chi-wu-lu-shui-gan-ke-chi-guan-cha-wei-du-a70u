from collections.abc import Iterator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .settings import DB_URL


class Base(DeclarativeBase):
    pass


engine = create_engine(DB_URL, pool_pre_ping=True)
open_session = sessionmaker(bind=engine, expire_on_commit=False)


def session_dependency() -> Iterator[Session]:
    with open_session() as db:
        yield db


def _apply_lightweight_migrations():
    inspector = inspect(engine)
    form_item_columns = {column["name"] for column in inspector.get_columns("form_items")}
    if "is_trial" not in form_item_columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE form_items ADD COLUMN is_trial BOOLEAN NOT NULL DEFAULT FALSE"))
    form_columns = {column["name"] for column in inspector.get_columns("forms")}
    if "trial_evidence" not in form_columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE forms ADD COLUMN trial_evidence JSON"))
    report_columns = {column["name"] for column in inspector.get_columns("factor_reports")}
    if "trials" not in report_columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE factor_reports ADD COLUMN trials JSON"))


def initialize():
    from . import cohort_models, identity_models, report_models, survey_models, form_models

    Base.metadata.create_all(engine)
    _apply_lightweight_migrations()

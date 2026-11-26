"""Database engine, session factory and first-run seed data (SQLite)."""
from __future__ import annotations

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import DATABASE_URL


class Base(DeclarativeBase):
    pass


engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    """FastAPI dependency: one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def seed_sections(db) -> None:
    """Create the pipeline (Depot A -> A -> B -> C -> Depot B) and its equipment once."""
    from app.models import Asset, PipelineSection

    if db.scalar(select(func.count(PipelineSection.id))) == 0:
        secs = [
            PipelineSection(name="Section A", start_point="Depot A", end_point="Point 1"),
            PipelineSection(name="Section B", start_point="Point 1", end_point="Point 2"),
            PipelineSection(name="Section C", start_point="Point 2", end_point="Depot B"),
        ]
        db.add_all(secs)
        db.flush()
        db.add_all([
            Asset(name="Pump P1", kind="PUMP", position=0, state="RUNNING", pipeline_section_id=secs[0].id),
            Asset(name="Inlet valve", kind="VALVE", position=0, state="OPEN", pipeline_section_id=secs[0].id),
            Asset(name="Valve A-B", kind="VALVE", position=1, state="OPEN", pipeline_section_id=secs[1].id),
            Asset(name="Valve B-C", kind="VALVE", position=2, state="OPEN", pipeline_section_id=secs[2].id),
            Asset(name="Outlet valve", kind="VALVE", position=3, state="OPEN", pipeline_section_id=secs[2].id),
        ])
        db.commit()


def init_db() -> None:
    from app import models  # noqa: F401  (registers the tables)

    Base.metadata.create_all(engine)
    with SessionLocal() as db:
        seed_sections(db)

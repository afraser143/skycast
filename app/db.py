"""Storage: the one profile (birth details and home city) and one saved reading per day."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime

from sqlalchemy import JSON, Date, DateTime, Float, Integer, String, Text, create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    pass


class Profile(Base):
    """There is only ever one row: Skycast is for one person."""

    __tablename__ = "profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    birth_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    birth_local: Mapped[str] = mapped_column(String(32))  # "1990-05-04T09:30", as entered
    birth_label: Mapped[str] = mapped_column(String(200))
    birth_lat: Mapped[float] = mapped_column(Float)
    birth_lon: Mapped[float] = mapped_column(Float)
    birth_tz: Mapped[str] = mapped_column(String(64))
    home_label: Mapped[str] = mapped_column(String(200))
    home_lat: Mapped[float] = mapped_column(Float)
    home_lon: Mapped[float] = mapped_column(Float)
    home_tz: Mapped[str] = mapped_column(String(64))
    fahrenheit: Mapped[bool] = mapped_column(default=True)


class DailyReading(Base):
    __tablename__ = "readings"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    focus: Mapped[str] = mapped_column(String(80))
    horoscope: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16))  # "ai" or "template"
    facts: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        if os.environ.get("VERCEL"):
            # Vercel's filesystem is thrown away between requests, so SQLite would lose everything.
            raise RuntimeError("DATABASE_URL must be set when running on Vercel")
        return "sqlite:///skycast.db"
    # Neon/Vercel hand out postgres:// or postgresql:// URLs; use the psycopg 3 driver for them.
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def make_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    # Two small tables and one user: creating them on start-up is simpler than a migration tool.
    Base.metadata.create_all(engine)
    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)

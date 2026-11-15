"""Storage: the people whose charts Skycast knows, and one saved reading per person per day."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import secrets
from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

log = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


class Person(Base):
    __tablename__ = "people"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(40))
    # The app owner: the person shown on "/" and the only one who can't be deleted.
    is_owner: Mapped[bool] = mapped_column(Boolean, default=False)
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
    fahrenheit: Mapped[bool] = mapped_column(Boolean, default=True)
    # A private share link is stored only as a SHA-256 hash, so the database can't reveal it.
    share_hash: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, default=None)
    share_created: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name.strip() else self.name


class Reading(Base):
    __tablename__ = "person_readings"

    person_id: Mapped[int] = mapped_column(ForeignKey("people.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    focus: Mapped[str] = mapped_column(String(80))
    horoscope: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16))  # "ai" or "template"
    facts: Mapped[dict] = mapped_column(JSON)
    messenger: Mapped[str | None] = mapped_column(String(40), default=None)
    messenger_message: Mapped[str | None] = mapped_column(String(200), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


# ---- Share links ---------------------------------------------------------------


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_share_link(session: Session, person: Person) -> str:
    """Creates (or replaces) a person's share link and returns the token.
    The token is shown once; only its hash is kept."""
    token = secrets.token_urlsafe(24)
    person.share_hash = hash_token(token)
    person.share_created = datetime.now(UTC)
    session.commit()
    return token


def person_for_token(session: Session, token: str) -> Person | None:
    if not token or len(token) > 100:
        return None
    return session.scalar(select(Person).where(Person.share_hash == hash_token(token)))


# ---- Connection and upgrade ----------------------------------------------------


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


def upgrade_from_single_user(engine: Engine) -> None:
    """Skycast v1 kept one person in `profile` and their readings in `readings`. Copy them
    into `people` / `person_readings` once. The old tables are left as they were."""
    tables = set(inspect(engine).get_table_names())
    if "profile" not in tables:
        return
    with Session(engine) as session:
        if session.scalar(select(Person.id).limit(1)) is not None:
            return  # already upgraded (or people added since)
        row = session.execute(text("SELECT * FROM profile WHERE id = 1")).mappings().first()
        if row is None:
            return
        fields = dict(row)
        if isinstance(fields["birth_utc"], str):  # SQLite returns raw DATETIME columns as text
            fields["birth_utc"] = datetime.fromisoformat(fields["birth_utc"]).replace(tzinfo=UTC)
        owner = Person(
            name="Me",
            is_owner=True,
            **{
                k: fields[k]
                for k in (
                    "birth_utc",
                    "birth_local",
                    "birth_label",
                    "birth_lat",
                    "birth_lon",
                    "birth_tz",
                    "home_label",
                    "home_lat",
                    "home_lon",
                    "home_tz",
                    "fahrenheit",
                )
            },  # fmt: skip
        )
        session.add(owner)
        session.flush()
        copied = 0
        if "readings" in tables:
            for r in session.execute(text("SELECT * FROM readings")).mappings():
                facts = r["facts"]
                if isinstance(facts, str):  # SQLite hands JSON back as text through a raw query
                    facts = json.loads(facts)
                day = r["day"] if isinstance(r["day"], date) else date.fromisoformat(str(r["day"]))
                session.add(
                    Reading(
                        person_id=owner.id,
                        day=day,
                        focus=r["focus"],
                        horoscope=r["horoscope"],
                        source=r["source"],
                        facts=facts,
                    )
                )
                copied += 1
        session.commit()
        log.info("[upgrade] copied the single-user profile and %d readings into people/person_readings", copied)


def add_missing_columns(engine: Engine) -> None:
    """`create_all` makes new tables but never changes existing ones. For a column added later
    (always nullable, so old rows stay valid), add it here. Safe to run on every start."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            have = {c["name"] for c in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in have:
                    continue
                if not column.nullable:
                    raise RuntimeError(f"New column {table.name}.{column.name} must be nullable to add automatically")
                type_sql = column.type.compile(dialect=engine.dialect)
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{column.name}" {type_sql}'))
                log.info("[upgrade] added column %s.%s", table.name, column.name)


def make_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    engine = create_engine(url, **kwargs)
    # A few small tables and one owner: creating them on start-up is simpler than a migration tool.
    Base.metadata.create_all(engine)
    add_missing_columns(engine)
    upgrade_from_single_user(engine)
    return engine


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)

import os
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import JSON, Boolean, CheckConstraint, Date, DateTime, ForeignKey, Index, LargeBinary, Numeric, String, Text, UniqueConstraint, URL, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

database_url = os.getenv("DATABASE_URL")
if not database_url and os.getenv("POSTGRES_PASSWORD"):
    database_url = URL.create("postgresql+psycopg", username="logbook", password=os.environ["POSTGRES_PASSWORD"],
                              host="db", database="logbook")
engine = create_engine(database_url or "sqlite:///logbook.sqlite", pool_pre_ping=True)
Session = sessionmaker(engine, expire_on_commit=False)
Amount = Numeric(24, 10)

def now():
    return datetime.now(timezone.utc)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password: Mapped[str] = mapped_column(String(200))
    admin: Mapped[bool] = mapped_column(Boolean)
    active: Mapped[bool] = mapped_column(Boolean, default=True)

class Login(Base):
    __tablename__ = "sessions"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    expires: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

class Vehicle(Base):
    __tablename__ = "vehicles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    registration: Mapped[str] = mapped_column(String(30), unique=True)
    fuel_type: Mapped[str] = mapped_column(String(60))
    kind: Mapped[str] = mapped_column(String(60))
    responsible: Mapped[str] = mapped_column(String(120))
    start_date: Mapped[date] = mapped_column(Date)
    initial_odo: Mapped[int]
    capacity: Mapped[Decimal] = mapped_column(Amount)
    initial_liters: Mapped[Decimal] = mapped_column(Amount)
    initial_net: Mapped[Decimal] = mapped_column(Amount)
    city_rate: Mapped[Decimal] = mapped_column(Amount)
    outside_rate: Mapped[Decimal] = mapped_column(Amount)
    city_limit: Mapped[int]
    imported_lock: Mapped[date | None] = mapped_column(Date)
    __table_args__ = (CheckConstraint("initial_odo >= 0 AND capacity > 0 AND initial_liters >= 0 AND initial_liters <= capacity AND initial_net >= 0 AND city_rate > 0 AND outside_rate > 0"),)

class Access(Base):
    __tablename__ = "vehicle_access"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), primary_key=True)

class Trip(Base):
    __tablename__ = "trips"
    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"))
    day: Mapped[date] = mapped_column(Date)
    start_odo: Mapped[int]
    end_odo: Mapped[int]
    origin: Mapped[str] = mapped_column(String(150))
    destination: Mapped[str] = mapped_column(String(150))
    purpose: Mapped[str] = mapped_column(String(150))
    category: Mapped[str] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(Text)
    rate: Mapped[Decimal] = mapped_column(Amount)
    legacy_row: Mapped[int | None]
    estimated: Mapped[bool] = mapped_column(Boolean, default=False)
    draft_id: Mapped[str | None] = mapped_column(ForeignKey("monthly_drafts.id"), index=True)
    __table_args__ = (Index("ix_trips_vehicle_day", "vehicle_id", "day"), UniqueConstraint("vehicle_id","day","start_odo","end_odo"), CheckConstraint("start_odo >= 0 AND end_odo > start_odo AND rate > 0"))

class Fuel(Base):
    __tablename__ = "fuel_purchases"
    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"))
    day: Mapped[date] = mapped_column(Date)
    # Receipts without a printed odometer stay empty instead of storing an invented value.
    odo: Mapped[int | None]
    liters: Mapped[Decimal] = mapped_column(Amount)
    gross: Mapped[Decimal] = mapped_column(Amount)
    vat: Mapped[Decimal] = mapped_column(Amount)
    station: Mapped[str] = mapped_column(String(150))
    clock: Mapped[str] = mapped_column(String(8))
    note: Mapped[str] = mapped_column(Text)
    measured: Mapped[Decimal | None] = mapped_column(Amount)
    legacy_row: Mapped[int | None]
    draft_id: Mapped[str | None] = mapped_column(ForeignKey("monthly_drafts.id"), index=True)
    __table_args__ = (Index("ix_fuel_vehicle_day","vehicle_id","day"), CheckConstraint("odo >= 0 AND liters > 0 AND gross > 0 AND vat BETWEEN 0 AND 100 AND (measured IS NULL OR measured >= 0)"))

class Destination(Base):
    __tablename__ = "destinations"
    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    name: Mapped[str] = mapped_column(String(150))
    origin: Mapped[str] = mapped_column(String(150))
    distance: Mapped[int]
    note: Mapped[str] = mapped_column(Text)
    __table_args__ = (UniqueConstraint("vehicle_id","name","origin"), CheckConstraint("distance > 0"))

class Holiday(Base):
    __tablename__ = "holidays"
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)

class Closing(Base):
    __tablename__ = "monthly_closings"
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), primary_key=True)
    month: Mapped[date] = mapped_column(Date, primary_key=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Audit(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    vehicle_id: Mapped[int | None] = mapped_column(ForeignKey("vehicles.id"), index=True)
    action: Mapped[str] = mapped_column(String(60))
    data: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class FuelInvoice(Base):
    """Zapísaná faktúra z tankovacích kariet; číslo faktúry bráni dvojitému zápisu."""
    __tablename__ = "fuel_invoices"
    number: Mapped[str] = mapped_column(String(40), primary_key=True)
    supplier: Mapped[str] = mapped_column(String(40))
    issued: Mapped[date | None] = mapped_column(Date)
    total: Mapped[Decimal] = mapped_column(Amount)
    written: Mapped[int]
    skipped: Mapped[int]
    imported_by: Mapped[str] = mapped_column(String(150))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Batch(Base):
    __tablename__ = "import_batches"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"))
    warnings: Mapped[list] = mapped_column(JSON)

class MonthlyDraft(Base):
    __tablename__ = "monthly_drafts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    month: Mapped[date] = mapped_column(Date)
    fingerprint: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class AssistantSession(Base):
    """Work-in-progress month for the chat wizard; nothing here is part of the logbook until approval."""
    __tablename__ = "assistant_sessions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    month: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), default="draft")
    version: Mapped[int] = mapped_column(default=1)
    state: Mapped[dict] = mapped_column(JSON)
    result: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint("vehicle_id", "user_id", "month"),)

class AssistantFile(Base):
    __tablename__ = "assistant_files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("assistant_sessions.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    mime: Mapped[str] = mapped_column(String(60))
    size: Mapped[int]
    sha256: Mapped[str] = mapped_column(String(64))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

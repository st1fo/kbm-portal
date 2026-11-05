from datetime import date as DateType, datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint, CheckConstraint, JSON, Index
from sqlalchemy.orm import Mapped, mapped_column
from .db import Base

def utcnow(): return datetime.now(timezone.utc)
class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    email: Mapped[str] = mapped_column(String(254), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default='employee')
    active: Mapped[bool] = mapped_column(default=True)
    daily_hours: Mapped[float] = mapped_column(default=8)
    __table_args__ = (CheckConstraint("role IN ('admin','employee')"), CheckConstraint('daily_hours > 0 AND daily_hours <= 24'))
class AttendanceType(Base):
    __tablename__ = 'attendance_types'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    icon: Mapped[str] = mapped_column(String(40))
    color: Mapped[str] = mapped_column(String(7))
    description: Mapped[str] = mapped_column(String(500), default='')
    requires_approval: Mapped[bool] = mapped_column(default=False)
    active: Mapped[bool] = mapped_column(default=True)
class Attendance(Base):
    __tablename__ = 'attendance_entries'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    date: Mapped[DateType]
    type_id: Mapped[int] = mapped_column(ForeignKey('attendance_types.id'))
    start: Mapped[str | None] = mapped_column(String(5))
    end: Mapped[str | None] = mapped_column(String(5))
    note: Mapped[str] = mapped_column(String(2000), default='')
    request_id: Mapped[int | None] = mapped_column(ForeignKey('leave_requests.id'))
    __table_args__ = (UniqueConstraint('user_id','date'), Index('ix_attendance_date', 'date'))
class LeaveRequest(Base):
    __tablename__ = 'leave_requests'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    type_id: Mapped[int] = mapped_column(ForeignKey('attendance_types.id'))
    start_date: Mapped[DateType]
    end_date: Mapped[DateType]
    note: Mapped[str] = mapped_column(String(2000), default='')
    status: Mapped[str] = mapped_column(String(20), default='pending')
    reason: Mapped[str] = mapped_column(String(2000), default='')
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint('end_date >= start_date'), CheckConstraint("status IN ('pending','approved','rejected')"))
class Holiday(Base):
    __tablename__ = 'public_holidays'
    date: Mapped[DateType] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
class Notification(Base):
    __tablename__ = 'notifications'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    message: Mapped[str] = mapped_column(String(500))
    read: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
class Audit(Base):
    __tablename__ = 'audit_logs'
    id: Mapped[int] = mapped_column(primary_key=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    action: Mapped[str] = mapped_column(String(100))
    detail: Mapped[str] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
class Setting(Base):
    __tablename__ = 'application_settings'
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)

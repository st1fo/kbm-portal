from datetime import date as DateType, datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, UniqueConstraint, CheckConstraint, JSON, Index, SmallInteger, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
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
    counts_as_work: Mapped[bool] = mapped_column(default=False)
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

# ---- Pracovný denník (journal): skutočný čas, projekty, aktivity, to-do. Migrácia 0003. ----
def name_key(value: str) -> str:
    """Kľúč na deduplikáciu názvov: orezané, zlúčené medzery, bez ohľadu na veľkosť písmen."""
    return ' '.join((value or '').split()).casefold()
class Customer(Base):
    __tablename__ = 'customers'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    name_key: Mapped[str] = mapped_column(String(150), unique=True)
    active: Mapped[bool] = mapped_column(default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
class Project(Base):
    __tablename__ = 'projects'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(150))
    name_key: Mapped[str] = mapped_column(String(150))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey('customers.id'))
    active: Mapped[bool] = mapped_column(default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
# NULL zákazník by obišiel UNIQUE(name_key, customer_id), preto výrazový index s COALESCE.
Index('ux_projects_key', Project.name_key, func.coalesce(Project.customer_id, 0), unique=True)
class Task(Base):
    __tablename__ = 'tasks'
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    project_id: Mapped[int | None] = mapped_column(ForeignKey('projects.id'), index=True)
    customer_id: Mapped[int | None] = mapped_column(ForeignKey('customers.id'))
    done: Mapped[bool] = mapped_column(default=False)
    active: Mapped[bool] = mapped_column(default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey('users.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint('project_id IS NULL OR customer_id IS NULL', name='ck_tasks_project_or_customer'),)
class WorkDay(Base):
    __tablename__ = 'work_days'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    date: Mapped[DateType]
    done: Mapped[bool] = mapped_column(default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    segments: Mapped[list['WorkSegment']] = relationship(order_by='WorkSegment.position', cascade='all, delete-orphan', passive_deletes=True)
    __table_args__ = (UniqueConstraint('user_id','date'),)
class WorkSegment(Base):
    __tablename__ = 'work_segments'
    id: Mapped[int] = mapped_column(primary_key=True)
    work_day_id: Mapped[int] = mapped_column(ForeignKey('work_days.id', ondelete='CASCADE'))
    position: Mapped[int] = mapped_column(SmallInteger)
    start: Mapped[str] = mapped_column(String(5))
    end: Mapped[str | None] = mapped_column(String(5))
    start_next_day: Mapped[bool] = mapped_column(default=False)
    end_next_day: Mapped[bool] = mapped_column(default=False)
    __table_args__ = (UniqueConstraint('work_day_id','position'),)
class Activity(Base):
    __tablename__ = 'activities'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    task_id: Mapped[int | None] = mapped_column(ForeignKey('tasks.id'), index=True)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    minutes: Mapped[int | None]
    text: Mapped[str] = mapped_column(String(5000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    __table_args__ = (CheckConstraint('minutes IS NULL OR minutes >= 0', name='ck_activities_minutes'), CheckConstraint('end_at IS NULL OR end_at > start_at', name='ck_activities_span'), Index('ix_activities_user_start','user_id','start_at'))
class Todo(Base):
    __tablename__ = 'todos'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    text: Mapped[str] = mapped_column(String(500))
    due: Mapped[DateType | None]
    priority: Mapped[int] = mapped_column(SmallInteger, default=0)
    project_id: Mapped[int | None] = mapped_column(ForeignKey('projects.id'))
    customer_id: Mapped[int | None] = mapped_column(ForeignKey('customers.id'))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    __table_args__ = (CheckConstraint('priority >= 0 AND priority <= 2', name='ck_todos_priority'), CheckConstraint('project_id IS NULL OR customer_id IS NULL', name='ck_todos_project_or_customer'))
class WorkPunch(Base):
    """Surové pípnutia; UNIQUE(user_id, client_id) robí offline frontu idempotentnou."""
    __tablename__ = 'work_punches'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    client_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(10))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    result: Mapped[str] = mapped_column(String(20))
    __table_args__ = (UniqueConstraint('user_id','client_id'), CheckConstraint("kind IN ('in','break','out','undo')", name='ck_work_punches_kind'))
class ImportMap(Base):
    """Pôvodné ID zo starého denníka → nové ID; zabezpečuje idempotentný import."""
    __tablename__ = 'journal_import_map'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'))
    kind: Mapped[str] = mapped_column(String(20))
    legacy_id: Mapped[str] = mapped_column(String(100))
    target_id: Mapped[int]
    __table_args__ = (UniqueConstraint('user_id','kind','legacy_id'),)

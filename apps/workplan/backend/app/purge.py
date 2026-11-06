"""Trvalé zmazanie používateľa a všetkých jeho údajov vo Workplane (volá iba portál s interným kľúčom).

Zmaže: plány dochádzky, žiadosti o absenciu, notifikácie, Denník (dni a úseky, aktivity, pípnutia, mapovanie
importu, osobné nástenky) a lokálny účet. Zdieľané záznamy ostanú: zákazníci/projekty/úlohy a karty stratia iba autora, tímovú
nástenku prevezme ďalší člen,
história zmien a schválenia cudzích žiadostí sa prepíšu na zástupný účet „Zmazaný používateľ“.
"""
import secrets
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from .db import get_db
from .models import (User, Attendance, LeaveRequest, Notification, Audit, Customer, Project, Task, WorkDay, WorkSegment,
                     Activity, WorkPunch, ImportMap, Board, BoardMember, Card, CardAssignee)
from .portal_sso import check_internal_key
from .security import hasher
from .common import audit

router = APIRouter(prefix='/api/internal', include_in_schema=False)
PLACEHOLDER_EMAIL = 'zmazany.pouzivatel@deleted.invalid'


class PurgeIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    actor_email: str | None = Field(default=None, max_length=254)


def placeholder(db):
    """Neaktívny zástupný účet, na ktorý sa prepíšu odkazy v zdieľanej histórii."""
    ph = db.scalar(select(User).where(User.email == PLACEHOLDER_EMAIL))
    if not ph:
        ph = User(name='Zmazaný používateľ', email=PLACEHOLDER_EMAIL, role='employee', active=False,
                  password_hash=hasher.hash(secrets.token_urlsafe(32)))
        db.add(ph)
        db.flush()
    return ph


def purge_user(db, email, actor_email=None):
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if not user or user.email == PLACEHOLDER_EMAIL:
        return {'found': False, 'counts': {}}
    uid = db.scalar(select(User.id).where(User.id == user.id).with_for_update())  # zámok riadku používateľa
    ph = placeholder(db)
    counts = {}

    def remove(label, stmt):
        counts[label] = db.execute(stmt.execution_options(synchronize_session=False)).rowcount or 0

    remove('plany', delete(Attendance).where(Attendance.user_id == uid))
    remove('ziadosti', delete(LeaveRequest).where(LeaveRequest.user_id == uid))
    remove('notifikacie', delete(Notification).where(Notification.user_id == uid))
    days = select(WorkDay.id).where(WorkDay.user_id == uid)
    remove('useky', delete(WorkSegment).where(WorkSegment.work_day_id.in_(days)))
    remove('dni_dochadzky', delete(WorkDay).where(WorkDay.user_id == uid))
    remove('aktivity', delete(Activity).where(Activity.user_id == uid))
    # nástenky: osobné zmazať, tímové odovzdať ďalšiemu členovi; členstvá a priradenia zrušiť
    from .boards import purge_board
    counts['nastenky'] = 0
    for b in db.scalars(select(Board).where(Board.owner_id == uid)).all():
        heir = db.scalar(select(BoardMember.user_id).where(BoardMember.board_id == b.id, BoardMember.user_id != uid).order_by(BoardMember.user_id))
        if heir is None:
            purge_board(db, b.id); counts['nastenky'] += 1
        else:
            b.owner_id = heir
            db.execute(delete(BoardMember).where(BoardMember.board_id == b.id, BoardMember.user_id == heir))
    db.flush()
    db.execute(delete(BoardMember).where(BoardMember.user_id == uid))
    db.execute(delete(CardAssignee).where(CardAssignee.user_id == uid))
    remove('pipnutia', delete(WorkPunch).where(WorkPunch.user_id == uid))
    db.execute(delete(ImportMap).where(ImportMap.user_id == uid))
    db.execute(update(LeaveRequest).where(LeaveRequest.reviewed_by == uid).values(reviewed_by=ph.id))
    for model in (Customer, Project, Task, Card):
        db.execute(update(model).where(model.created_by == uid).values(created_by=None))
    db.execute(update(Audit).where(Audit.actor_id == uid).values(actor_id=ph.id))
    db.execute(delete(User).where(User.id == uid))
    actor = db.scalar(select(User).where(User.email == (actor_email or '').lower())) if actor_email else None
    audit(db, actor or ph, 'user.purge', {'email': user.email, 'name': user.name, **counts})
    db.commit()
    return {'found': True, 'counts': counts}


@router.post('/purge-user')
def purge_endpoint(p: PurgeIn, request: Request, db: Session = Depends(get_db)):
    check_internal_key(request)
    return purge_user(db, p.email, p.actor_email)

"""Trvalé zmazanie používateľa v Knihe jázd (volá iba portál s interným kľúčom).

Zmaže prihlásenia, priradenie vozidiel, rozpracované návrhy a konverzácie asistenta (aj nahraté bločky) a lokálny
účet. Jazdy a tankovania patria vozidlu, preto ostávajú; schválené návrhy, na ktoré sa odkazujú, a história
zmien sa prepíšu na zástupný účet „zmazany-pouzivatel“.
"""
import secrets
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, update, or_
from .auth import db_session, hash_password
from .db import Access, Audit, AssistantFile, AssistantSession, Fuel, Login, MonthlyDraft, Trip, User
from .portal_sso import check_internal_key

router = APIRouter(prefix="/api/internal", include_in_schema=False)
PLACEHOLDER = "zmazany-pouzivatel"


class PurgeIn(BaseModel):
    username: str = Field(min_length=1, max_length=80)


def placeholder(db):
    ph = db.scalar(select(User).where(User.username == PLACEHOLDER))
    if not ph:
        ph = User(username=PLACEHOLDER, password=hash_password(secrets.token_urlsafe(32)), admin=False, active=False)
        db.add(ph)
        db.flush()
    return ph


def purge_user(db, username):
    user = db.scalar(select(User).where(User.username == username.strip().lower()).with_for_update())
    if not user or user.username == PLACEHOLDER:
        return {"found": False, "counts": {}}
    uid, ph, counts = user.id, placeholder(db), {}
    run = lambda stmt: db.execute(stmt.execution_options(synchronize_session=False)).rowcount or 0
    run(delete(Login).where(Login.user_id == uid))
    counts["vozidla"] = run(delete(Access).where(Access.user_id == uid))
    sessions = select(AssistantSession.id).where(AssistantSession.user_id == uid)
    counts["blocky"] = run(delete(AssistantFile).where(AssistantFile.session_id.in_(sessions)))
    counts["asistent"] = run(delete(AssistantSession).where(AssistantSession.user_id == uid))
    used = select(Trip.draft_id).where(Trip.draft_id.is_not(None)).union(select(Fuel.draft_id).where(Fuel.draft_id.is_not(None)))
    counts["navrhy"] = run(delete(MonthlyDraft).where(MonthlyDraft.user_id == uid, MonthlyDraft.id.not_in(used)))
    run(update(MonthlyDraft).where(MonthlyDraft.user_id == uid).values(user_id=ph.id))   # schválené: jazdy na ne odkazujú
    run(update(Audit).where(Audit.user_id == uid).values(user_id=ph.id))
    run(delete(User).where(User.id == uid))
    db.add(Audit(user_id=ph.id, vehicle_id=None, action="user.purge", data={"username": username, **counts}))
    db.commit()
    return {"found": True, "counts": counts}


@router.post("/purge-user")
def purge_endpoint(p: PurgeIn, request: Request, db=Depends(db_session)):
    check_internal_key(request)
    return purge_user(db, p.username)

"""Shared helpers for main.py and journal.py (moved from main.py without behaviour changes)."""
import calendar
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .models import User, Holiday, Audit, Setting

TZ=ZoneInfo('Europe/Bratislava')
def today(): return datetime.now(TZ).date()
def row(obj):
    return {c.name:getattr(obj,c.name) for c in obj.__table__.columns if c.name!='password_hash'}
def audit(db,user,action,detail): db.add(Audit(actor_id=user.id,action=action,detail=str(detail)[:2000]))
def commit(db):
    try: db.commit()
    except IntegrityError:
        db.rollback(); raise HTTPException(409,'Záznam koliduje s existujúcimi údajmi. Obnovte stránku.')
def owner(user,uid):
    if user.role!='admin' and user.id!=uid: raise HTTPException(403,'Môžete upravovať iba vlastné údaje.')
def employee(db,uid):
    u=db.scalar(select(User).where(User.id==uid).with_for_update())
    if not u or not u.active: raise HTTPException(404,'Aktívny zamestnanec neexistuje.')
    return u
def days(start,end):
    for i in range((end-start).days+1): yield start+timedelta(days=i)
def month_bounds(month):
    try:
        d=date.fromisoformat(month+'-01'); return d,d.replace(day=calendar.monthrange(d.year,d.month)[1])
    except ValueError: raise HTTPException(422,'Mesiac má formát RRRR-MM.')
def get_settings(db):
    s=db.get(Setting,'schedule'); return s.value if s else {'daily_hours':8,'weekends':[5,6]}
def working_dates(db,start,end):
    holidays=set(db.scalars(select(Holiday.date).where(Holiday.date.between(start,end))))
    return [d for d in days(start,end) if d.weekday() not in get_settings(db)['weekends'] and d not in holidays]

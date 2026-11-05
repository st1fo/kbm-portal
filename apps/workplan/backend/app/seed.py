"""Explicit, idempotent development data. Never run automatically in production."""
import os
from datetime import date, timedelta
import holidays
from sqlalchemy import select
from .db import SessionLocal
from .models import User, AttendanceType, Attendance, Holiday, Setting, LeaveRequest
from .security import hasher

def seed():
    password=os.environ.get('DEMO_PASSWORD','')
    if len(password)<10: raise RuntimeError('Set DEMO_PASSWORD (10+ characters) to initialize demo accounts.')
    with SessionLocal() as db:
        if db.scalar(select(User)): print('Database has users; demo seed skipped.'); return
        names=[('Maroš Sokolák','maros'),('Zdenko Sokolovský','zdenko'),('Florián Žifčák','florian'),('Martin Zelenák','martin')]
        users=[]
        for i,(name,slug) in enumerate(names):
            u=User(name=name,email=f'{slug}@demo.kbm.local',password_hash=hasher.hash(password),role='admin' if i==0 else 'employee'); db.add(u); users.append(u)
        from .init_data import init_reference
        init_reference(db)
        db.flush()
        for i,u in enumerate(users):
            for day in range(1,31):
                d=date(2026,11,day)
                if d.weekday()>4: continue
                tid=2 if (day+i*2)%5 in [0,1] else 1
                if i==1 and day in [9,10,11]: tid=3
                if i==2 and day in [5,6]: tid=4
                if i==0 and day==12: tid=5
                if i==3 and day in [16,17]: tid=6
                db.add(Attendance(user_id=u.id,date=d,type_id=tid,start='08:00' if tid in [1,2,6] else None,end='16:30' if tid in [1,2,6] else None,note='Fiktívne demo údaje'))
        db.add(LeaveRequest(user_id=users[2].id,type_id=3,start_date=date(2026,11,23),end_date=date(2026,11,25),note='Fiktívna žiadosť o dovolenku'))
        db.commit(); print('Four demo users and November 2026 initialized.')
if __name__=='__main__': seed()

from sqlalchemy import select
import holidays
from .db import SessionLocal
from .models import AttendanceType, Setting, Holiday
# Kategórie, ktoré sa v Denníku rátajú ako pracovný deň (očakávané hodiny > 0).
WORK_ICONS=('Building2','House','Plane','GraduationCap')
def init_reference(db):
    types=[('Kancelária','Building2','#2f806c',False),('Práca z domu','House','#557cb7',False),('Dovolenka','Palmtree','#c19437',True),('PN','HeartPulse','#cb6870',True),('Lekár','Stethoscope','#aa79be',True),('Služobná cesta','Plane','#49a0af',True),('Školenie','GraduationCap','#7b77c8',True),('Voľno','CalendarOff','#89939c',True),('Sviatok','CalendarDays','#bd945b',True),('Iné','CircleHelp','#8a8b91',True)]
    if not db.scalar(select(AttendanceType)):
        for name,icon,color,approval in types: db.add(AttendanceType(name=name,icon=icon,color=color,requires_approval=approval,counts_as_work=icon in WORK_ICONS))
    if not db.get(Setting,'schedule'): db.add(Setting(key='schedule',value={'daily_hours':8,'weekends':[5,6]}))
    if not db.scalar(select(Holiday)):
        # python-holidays distinguishes days of rest; explicitly exclude 2026 exceptions.
        for y in range(2025,2031):
            for d,label in holidays.SK(years=y,language='sk').items():
                if (d.month,d.day) in [(9,1),(10,28),(11,17)] or (y==2026 and (d.month,d.day) in [(5,8),(9,15)]): continue
                db.add(Holiday(date=d,name=label))

if __name__=='__main__':
    with SessionLocal() as db:
        init_reference(db)
        db.commit()
    print('Reference data initialized; existing settings preserved.')

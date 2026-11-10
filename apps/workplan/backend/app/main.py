import csv, io
from collections import defaultdict
from datetime import date, datetime
from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from openpyxl import Workbook
from .db import get_db
from .models import User, AttendanceType, Attendance, LeaveRequest, Holiday, Notification, Audit, Setting
from .schemas import Employee, TypeInput, Plan, Review, SettingsInput, HolidayInput
from .security import current_user, admin
from .common import TZ, today, row, private_row, audit, commit, owner, employee, month_bounds, get_settings, working_dates, is_work
from .journal import router as journal_router
from .boards import router as boards_router
from .pages import router as pages_router, allowed_pages
from .journal_ai import router as ai_router
from .hours import router as hours_router
from .purge import router as purge_router
from .directory import visible_user_ids, group_names, restrict_to_group

app = FastAPI(title='KBM Workplan API',version='1.0.0')
app.include_router(journal_router)
app.include_router(boards_router)
app.include_router(pages_router)
app.include_router(ai_router)
app.include_router(hours_router)
app.include_router(purge_router)
def notify_admins(db,message):
    for uid in db.scalars(select(User.id).where(User.role=='admin',User.active==True)):
        db.add(Notification(user_id=uid,message=message))
def request_conflict(db,uid,start,end,exclude=None,partial_ok=False):
    """partial_ok: schválená absencia na časť dňa plán dňa nenahrádza, preto zápisu plánu neprekáža."""
    q=select(LeaveRequest).where(LeaveRequest.user_id==uid,LeaveRequest.status.in_(['pending','approved']),LeaveRequest.start_date<=end,LeaveRequest.end_date>=start)
    if exclude: q=q.where(LeaveRequest.id!=exclude)
    if partial_ok: q=q.where((LeaveRequest.hours.is_(None))|(LeaveRequest.status=='pending'))
    if db.scalar(q): raise HTTPException(409,'V rozsahu už existuje žiadosť. Najprv ju zrušte.')
def full_day_absence(db,uid,d):
    e=db.scalar(select(Attendance).where(Attendance.user_id==uid,Attendance.date==d))
    t=db.get(AttendanceType,e.type_id) if e else None
    if t and not is_work(t): raise HTTPException(409,f'Dňa {d:%d.%m.%Y} je už celodenná absencia ({t.name}).')
def apply_plan(db,p,request_id=None):
    # rozsah dní zapíše iba pracovné dni (inak by víkendy nafúkli výkaz hodín); jeden deň sa dá naplánovať aj na víkend
    dates=working_dates(db,p.start_date,p.end_date) if request_id or p.start_date!=p.end_date else [p.start_date]
    if not dates: raise HTTPException(422,'Rozsah neobsahuje pracovný deň.')
    for d in dates:
        e=db.scalar(select(Attendance).where(Attendance.user_id==p.user_id,Attendance.date==d).with_for_update())
        if e and (not p.replace or e.request_id): raise HTTPException(409,f'Dňa {d:%d.%m.%Y} už existuje záznam. Potvrďte jeho nahradenie alebo zrušte schválenú žiadosť.')
        if not e: e=Attendance(user_id=p.user_id,date=d); db.add(e)
        e.type_id=p.type_id; e.start=p.start; e.end=p.end; e.note=p.note; e.request_id=request_id

@app.get('/api/health')
def health(db:Session=Depends(get_db)):
    db.execute(select(1)); return {'status':'ok'}
@app.middleware('http')
async def portal_csrf(request, call_next):
    from fastapi.responses import JSONResponse
    from urllib.parse import urlsplit
    if request.method in {'POST','PUT','PATCH','DELETE'}:
        origin=request.headers.get('origin')
        if request.headers.get('sec-fetch-site') in {'cross-site','same-site'} or (origin and urlsplit(origin).netloc != request.headers.get('host')):
            return JSONResponse({'detail':'Nepovolený pôvod požiadavky.'},status_code=403)
    response=await call_next(request)
    response.headers['Cache-Control']='no-store'
    return response

@app.post('/api/auth/login')
def login():
    raise HTTPException(410, 'Použite spoločné prihlásenie na hlavnej stránke KBM.')
@app.get('/api/auth/me')
def me(u=Depends(current_user),db:Session=Depends(get_db)): return {**row(u),'pages':allowed_pages(db,u)}
@app.get('/api/employees')
def employees(u=Depends(current_user),db:Session=Depends(get_db)):
    vis=visible_user_ids(db,u); groups=group_names(db)
    q=select(User).order_by(User.id)
    if u.role!='admin': q=q.where(User.active==True)
    if vis is not None: q=q.where(User.id.in_(vis))
    return [{**row(x),'groups':groups.get(x.email,[])} for x in db.scalars(q)]
@app.post('/api/employees')
def add_employee(p:Employee,u=Depends(admin),db:Session=Depends(get_db)):
    raise HTTPException(409,'Účty sa pridávajú v portáli KBM (Správa používateľov).')
@app.put('/api/employees/{uid}')
def edit_employee(uid:int,p:Employee,u=Depends(admin),db:Session=Depends(get_db)):
    obj=db.get(User,uid)
    if not obj: raise HTTPException(404,'Zamestnanec neexistuje.')
    if uid==u.id and (not p.active or p.role!='admin'): raise HTTPException(422,'Vlastný administrátorský účet nemožno deaktivovať ani zmeniť jeho rolu.')
    if p.email != obj.email or p.name != obj.name or p.role != obj.role: raise HTTPException(409,'Identitu a rolu spravuje spoločný portál.')
    for k,v in p.model_dump(exclude={'password'}).items(): setattr(obj,k,v)
    obj.email=obj.email.lower().strip()
    if p.password: raise HTTPException(409,'Heslo zmeňte v nastaveniach portálu.')
    audit(db,u,'employee.update',uid); commit(db); return row(obj)
@app.get('/api/types')
def types(u=Depends(current_user),db:Session=Depends(get_db)): return [row(x) for x in db.scalars(select(AttendanceType).order_by(AttendanceType.id))]
@app.post('/api/types')
def add_type(p:TypeInput,u=Depends(admin),db:Session=Depends(get_db)):
    t=AttendanceType(**p.model_dump()); db.add(t); audit(db,u,'type.create',p.name); commit(db); return row(t)
@app.put('/api/types/{tid}')
def edit_type(tid:int,p:TypeInput,u=Depends(admin),db:Session=Depends(get_db)):
    t=db.get(AttendanceType,tid)
    if not t: raise HTTPException(404,'Kategória neexistuje.')
    for k,v in p.model_dump().items(): setattr(t,k,v)
    audit(db,u,'type.update',tid); commit(db); return row(t)
@app.get('/api/calendar')
def calendar_data(month:str,u=Depends(current_user),db:Session=Depends(get_db)):
    start,end=month_bounds(month); vis=visible_user_ids(db,u)
    entries=select(Attendance).where(Attendance.date.between(start,end)); pending=select(LeaveRequest).where(LeaveRequest.status=='pending',LeaveRequest.start_date<=end,LeaveRequest.end_date>=start)
    partial=select(LeaveRequest).where(LeaveRequest.status=='approved',LeaveRequest.hours.is_not(None),LeaveRequest.start_date.between(start,end))
    if vis is not None: entries=entries.where(Attendance.user_id.in_(vis)); pending=pending.where(LeaveRequest.user_id.in_(vis)); partial=partial.where(LeaveRequest.user_id.in_(vis))
    work={t.id for t in db.scalars(select(AttendanceType)) if is_work(t)}
    return {'entries':[private_row(x,u,work) for x in db.scalars(entries)], 'requests':[private_row(x,u) for x in db.scalars(pending)], 'partial':[private_row(x,u) for x in db.scalars(partial)], 'holidays':[row(x) for x in db.scalars(select(Holiday).where(Holiday.date.between(start,end)))], 'settings':get_settings(db)}
@app.post('/api/attendance')
def save_attendance(p:Plan,u=Depends(current_user),db:Session=Depends(get_db)):
    owner(u,p.user_id); employee(db,p.user_id)
    t=db.get(AttendanceType,p.type_id)
    if not t or not t.active: raise HTTPException(422,'Neaktívna alebo neexistujúca kategória.')
    if t.requires_approval or (u.role!='admin' and t.icon not in ['Building2','House']): raise HTTPException(403,'Na túto absenciu odošlite žiadosť.')
    request_conflict(db,p.user_id,p.start_date,p.end_date,partial_ok=is_work(t))
    apply_plan(db,p); audit(db,u,'attendance.save',p.model_dump()); commit(db); return {'ok':True}
@app.delete('/api/attendance/{aid}')
def delete_attendance(aid:int,u=Depends(current_user),db:Session=Depends(get_db)):
    e=db.get(Attendance,aid)
    if not e: raise HTTPException(404,'Záznam neexistuje.')
    owner(u,e.user_id)
    t=db.get(AttendanceType,e.type_id)
    if e.request_id: raise HTTPException(409,'Zrušte súvisiacu schválenú žiadosť.')
    if u.role!='admin' and t.icon not in ['Building2','House']: raise HTTPException(403,'Túto absenciu môže upraviť administrátor.')
    db.delete(e); audit(db,u,'attendance.delete',aid); commit(db); return {'ok':True}
@app.get('/api/requests')
def requests(u=Depends(current_user),db:Session=Depends(get_db)):
    q=select(LeaveRequest).order_by(LeaveRequest.created_at.desc())
    if u.role!='admin': q=q.where(LeaveRequest.user_id==u.id)
    names=dict(db.execute(select(User.id,User.name)).all())   # kto žiadosť vybavil, aj keď nie je v skupine žiadateľa
    return [{**row(x),'reviewed_by_name':names.get(x.reviewed_by)} for x in db.scalars(q)]
@app.post('/api/requests')
def add_request(p:Plan,u=Depends(current_user),db:Session=Depends(get_db)):
    owner(u,p.user_id); employee(db,p.user_id)
    t=db.get(AttendanceType,p.type_id)
    if not t or not t.active or not t.requires_approval: raise HTTPException(422,'Kategória nevyžaduje žiadosť.')
    request_conflict(db,p.user_id,p.start_date,p.end_date)
    if not working_dates(db,p.start_date,p.end_date): raise HTTPException(422,'Rozsah neobsahuje pracovný deň.')
    if p.hours is not None:
        person=db.get(User,p.user_id)
        if p.hours>=person.daily_hours: raise HTTPException(422,f'Absencia na {p.hours:g} h trvá celý deň ({person.daily_hours:g} h). Zrušte počet hodín.')
        full_day_absence(db,p.user_id,p.start_date)
    r=LeaveRequest(user_id=p.user_id,type_id=p.type_id,start_date=p.start_date,end_date=p.end_date,hours=p.hours,note=p.note)
    db.add(r); notify_admins(db,f'{u.name}: nová žiadosť o absenciu.'); audit(db,u,'request.create',p.model_dump()); commit(db); return row(r)
@app.post('/api/requests/{rid}/review')
def review(rid:int,p:Review,u=Depends(admin),db:Session=Depends(get_db)):
    r=db.scalar(select(LeaveRequest).where(LeaveRequest.id==rid).with_for_update())
    if not r: raise HTTPException(404,'Žiadosť neexistuje.')
    # vlastnú žiadosť vybaví iný administrátor; jediný administrátor by inak svoju absenciu nezapísal nikdy
    if r.user_id==u.id and db.scalar(select(User.id).where(User.role=='admin',User.active==True,User.id!=u.id).limit(1)):
        raise HTTPException(403,'Vlastnú žiadosť nemôžete schváliť ani zamietnuť.')
    if r.status!='pending': raise HTTPException(409,'Žiadosť už bola vybavená.')
    if p.status=='approved':
        employee(db,r.user_id)
        # časť dňa plán nenahrádza (deň ostáva napr. Kancelária), výkaz hodín ju odpočíta z odpracovaného času
        if r.hours is not None: full_day_absence(db,r.user_id,r.start_date)
        else: apply_plan(db,Plan(user_id=r.user_id,type_id=r.type_id,start_date=r.start_date,end_date=r.end_date,note=r.note,replace=True),r.id)
    r.status=p.status; r.reason=p.reason; r.reviewed_by=u.id; r.reviewed_at=datetime.now(TZ)
    db.add(Notification(user_id=r.user_id,message=(f'Vaša žiadosť {r.start_date:%d.%m.} – {r.end_date:%d.%m.}: '+('schválená' if p.status=='approved' else 'zamietnutá')+'. '+p.reason)[:500]))
    audit(db,u,'request.'+p.status,rid); commit(db); return row(r)
@app.delete('/api/requests/{rid}')
def cancel_request(rid:int,u=Depends(current_user),db:Session=Depends(get_db)):
    r=db.scalar(select(LeaveRequest).where(LeaveRequest.id==rid).with_for_update())
    if not r: raise HTTPException(404,'Žiadosť neexistuje.')
    owner(u,r.user_id)
    if r.status=='approved' and u.role!='admin': raise HTTPException(403,'Schválenú žiadosť môže zrušiť administrátor.')
    if r.status not in ('pending','approved'): raise HTTPException(409,'Žiadosť už bola vybavená.')   # rozhodnutie admina sa neprepisuje
    for e in db.scalars(select(Attendance).where(Attendance.request_id==rid)): db.delete(e)
    db.flush(); r.status='rejected'; r.reason='Zrušené'; r.reviewed_by=u.id; r.reviewed_at=datetime.now(TZ)
    db.add(Notification(user_id=r.user_id,message='Žiadosť bola zrušená.')); audit(db,u,'request.cancel',rid); commit(db); return {'ok':True}
@app.get('/api/statistics')
def statistics(month:str,group:str|None=None,u=Depends(current_user),db:Session=Depends(get_db)):
    start,end=month_bounds(month); vis=restrict_to_group(db,visible_user_ids(db,u),group)
    all_users=[x for x in db.scalars(select(User).where(User.active==True)) if vis is None or x.id in vis]
    counts=defaultdict(int)
    for e in db.scalars(select(Attendance).where(Attendance.date==today())):
        if vis is None or e.user_id in vis: counts[db.get(AttendanceType,e.type_id).icon]+=1
    upcoming=select(LeaveRequest).where(LeaveRequest.status=='approved',LeaveRequest.end_date>=today()).order_by(LeaveRequest.start_date).limit(10)
    if vis is not None: upcoming=upcoming.where(LeaveRequest.user_id.in_(vis))
    return {'employees':len(all_users),'office':counts['Building2'],'home':counts['House'],'vacation':counts['Palmtree'],'absent':sum(v for k,v in counts.items() if k not in ['Building2','House','Palmtree']),'upcoming':[private_row(x,u) for x in db.scalars(upcoming)]}
@app.get('/api/notifications')
def notifications(u=Depends(current_user),db:Session=Depends(get_db)):
    return [row(x) for x in db.scalars(select(Notification).where(Notification.user_id==u.id).order_by(Notification.id.desc()).limit(50))]
@app.post('/api/notifications/read')
def read_notifications(u=Depends(current_user),db:Session=Depends(get_db)):
    for n in db.scalars(select(Notification).where(Notification.user_id==u.id,Notification.read==False)): n.read=True
    commit(db); return {'ok':True}
@app.get('/api/settings')
def settings(u=Depends(current_user),db:Session=Depends(get_db)): return get_settings(db)
@app.put('/api/settings')
def update_settings(p:SettingsInput,u=Depends(admin),db:Session=Depends(get_db)):
    s=db.get(Setting,'schedule')
    if not s: s=Setting(key='schedule'); db.add(s)
    s.value=p.model_dump(); audit(db,u,'settings.update',p.model_dump()); commit(db); return s.value
@app.get('/api/holidays')
def holidays(year:int,u=Depends(current_user),db:Session=Depends(get_db)):
    if year<2000 or year>2100: raise HTTPException(422,'Rok musí byť 2000 až 2100.')
    return [row(x) for x in db.scalars(select(Holiday).where(Holiday.date.between(date(year,1,1),date(year,12,31))).order_by(Holiday.date))]
@app.put('/api/holidays')
def put_holiday(p:HolidayInput,u=Depends(admin),db:Session=Depends(get_db)):
    h=db.get(Holiday,p.date)
    if not h: h=Holiday(date=p.date); db.add(h)
    h.name=p.name; audit(db,u,'holiday.save',p.date); commit(db); return row(h)
@app.delete('/api/holidays/{day}')
def delete_holiday(day:date,u=Depends(admin),db:Session=Depends(get_db)):
    h=db.get(Holiday,day)
    if not h: raise HTTPException(404,'Sviatok neexistuje.')
    db.delete(h); audit(db,u,'holiday.delete',day); commit(db); return {'ok':True}
@app.get('/api/audit')
def audit_logs(u=Depends(admin),db:Session=Depends(get_db)):
    return [row(x) for x in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(100))]
@app.get('/api/reports')
def report(start:date,end:date,format:str='csv',user_id:int|None=None,group:str|None=None,u=Depends(admin),db:Session=Depends(get_db)):
    if end<start or (end-start).days>366: raise HTTPException(422,'Neplatný rozsah dátumov.')
    if format not in ['csv','xlsx']: raise HTTPException(422,'Podporované: csv, xlsx.')
    team=restrict_to_group(db,None,group)
    users=[x for x in db.scalars(select(User).where(User.id==user_id) if user_id else select(User).order_by(User.name)) if team is None or x.id in team]
    types={t.id:t.name for t in db.scalars(select(AttendanceType))}
    table=[['Zamestnanec','Dátum','Plán','Poznámka']]
    for person in users:
        plans={e.date:e for e in db.scalars(select(Attendance).where(Attendance.user_id==person.id,Attendance.date.between(start,end)))}
        for d in sorted(plans):
            p=plans[d]
            table.append([person.name,d.strftime('%d.%m.%Y'),types[p.type_id] if p else '',p.note])
    if format=='csv':
        buf=io.StringIO(); csv.writer(buf,delimiter=';').writerows([["'"+v if isinstance(v,str) and v.lstrip()[:1] in ('=','+','-','@') else v for v in r] for r in table]); data=('\ufeff'+buf.getvalue()).encode(); mime='text/csv; charset=utf-8'
    else:
        wb=Workbook(); ws=wb.active; ws.title='Dochádzka'
        for r in table:
            ws.append(r)
            for cell in ws[ws.max_row]:
                if isinstance(cell.value,str): cell.data_type='s'
        ws.freeze_panes='A2'; ws.auto_filter.ref=ws.dimensions
        summary=wb.create_sheet('Súhrn')
        summary.append(['Zamestnanec'] + list(types.values()))
        for person in users:
            counts=defaultdict(int)
            for e in db.scalars(select(Attendance).where(Attendance.user_id==person.id,Attendance.date.between(start,end))): counts[e.type_id]+=1
            summary.append([person.name] + [counts[tid] for tid in types])
        summary.freeze_panes='A2'; summary.auto_filter.ref=summary.dimensions
        for cell in summary['A']:
            if isinstance(cell.value,str): cell.data_type='s'
        from openpyxl.styles import Font, PatternFill
        for cell in list(ws[1])+list(summary[1]): cell.font=Font(bold=True,color='FFFFFF'); cell.fill=PatternFill('solid',fgColor='26342F')
        for col in ws.columns: ws.column_dimensions[col[0].column_letter].width=min(50,max(len(str(c.value or '')) for c in col)+3)
        out=io.BytesIO(); wb.save(out); data=out.getvalue(); mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    audit(db,u,'report.export',f'{start} {end} {format}'); commit(db)
    return StreamingResponse(iter([data]),media_type=mime,headers={'Content-Disposition':f'attachment; filename="kbm-dochadzka-{start}.{format}"'})

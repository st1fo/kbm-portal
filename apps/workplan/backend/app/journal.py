"""Denník: skutočný odpracovaný čas (úseky), zdieľané zákazníky/projekty/úlohy, aktivity a to-do.

Oprávnenia: dochádzka, aktivity a to-do sú osobné (zamestnanec iba svoje, admin všetko + audit).
Zákazníkov, projekty a úlohy vytvára a upravuje každý aktívny používateľ; deaktivuje a maže iba admin,
mazať sa dá len nepoužitý záznam.
"""
from datetime import date, datetime, time, timedelta, timezone
from typing import Literal
from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func, delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from .db import get_db
from .models import User, Customer, Project, Task, WorkDay, WorkSegment, Activity, Todo, WorkPunch, name_key
from .security import current_user
from .common import TZ, audit, commit, owner, employee, month_bounds
from . import journal_calc as jc

router = APIRouter(prefix='/api/journal', tags=['journal'])
HM = r'^([01]\d|2[0-3]):[0-5]\d$'
PUNCH_FUTURE = timedelta(minutes=2)   # tolerancia hodín zariadenia
PUNCH_PAST = timedelta(days=7)        # najstaršie pípnutie z offline fronty


def now() -> datetime:
    """Aktuálny miestny čas; v testoch sa nahrádza."""
    return datetime.now(TZ)


def local(dt: datetime | None) -> datetime | None:
    """SQLite vracia naivné UTC, PostgreSQL aware; navonok vždy Europe/Bratislava."""
    if dt is None: return None
    if dt.tzinfo is None: dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(TZ)


def utc(dt: datetime | None) -> datetime | None:
    """Do DB vždy UTC (SQLite by inak uložil miestny nástenný čas bez pásma)."""
    return dt.astimezone(timezone.utc) if dt is not None else None


def clean_name(value: str) -> str:
    return ' '.join((value or '').replace('\x00', '').split())


def target_user(u: User, user_id: int | None, db: Session) -> int:
    """Čí záznam čítam/píšem: vlastný, alebo (len admin) iného zamestnanca."""
    if user_id is None or user_id == u.id: return u.id
    owner(u, user_id)
    if not db.get(User, user_id): raise HTTPException(404, 'Zamestnanec neexistuje.')
    return user_id


def audit_foreign(db, u, uid, action, detail):
    """Zmeny cudzích osobných záznamov (robí ich iba admin) idú do auditu."""
    if uid != u.id: audit(db, u, action, {'user_id': uid, **detail} if isinstance(detail, dict) else f'user {uid}: {detail}')


# ---------------------------------------------------------------- dochádzka

def rec_of(wd: WorkDay | None):
    if not wd or not wd.segments: return None
    return {'segs': [{'in': s.start, 'out': s.end} for s in wd.segments], 'done': wd.done}


def load_days(db, uid, start: date, end: date) -> dict:
    return {wd.date: wd for wd in db.scalars(select(WorkDay).where(WorkDay.user_id == uid, WorkDay.date.between(start, end)))}


def records_around(db, uid, n: datetime) -> dict:
    """Záznamy potrebné na activeKey(): dnešok a včerajšok."""
    return {d: rec_of(wd) for d, wd in load_days(db, uid, n.date() - timedelta(days=1), n.date()).items()}


def day_out(day: date, rec, n: datetime, active: date) -> dict:
    segs = rec['segs'] if rec else []
    c = jc.calc_day(day, rec, n, active)
    u = jc.unwrap(segs)
    last_out = None
    for x in u:
        if x['out'] is not None: last_out = x['out']
    first, _ = jc.min_to_hm(c['first'])
    last, last_nd = jc.min_to_hm(None if c['open'] else last_out)
    return {
        'date': day.isoformat(), 'done': bool(rec and rec['done']), 'status': jc.status_of(rec),
        'segments': [{'start': s['in'], 'end': s['out'], 'start_next_day': x['in'] is not None and x['in'] >= 1440,
                      'end_next_day': x['out'] is not None and x['out'] >= 1440} for s, x in zip(segs, u)],
        'worked_min': round(c['work']), 'break_min': round(c['brk']), 'count': c['count'], 'open': c['open'],
        'break_running': c['break_running'], 'first': first, 'last': last, 'last_next_day': last_nd,
    }


def store(db, uid: int, day: date, rec) -> None:
    """Uloží (alebo zmaže) deň; príznaky +1 deň dopočíta server z poradia časov."""
    wd = db.scalar(select(WorkDay).where(WorkDay.user_id == uid, WorkDay.date == day))
    if not rec or not rec['segs']:
        if wd: db.delete(wd)
        return
    if not wd:
        wd = WorkDay(user_id=uid, date=day)
        db.add(wd)
    wd.done = bool(rec['done'])
    wd.segments.clear()
    db.flush()  # staré pozície preč skôr, než vložíme nové (UNIQUE work_day_id, position)
    for i, (s, x) in enumerate(zip(rec['segs'], jc.unwrap(rec['segs']))):
        wd.segments.append(WorkSegment(position=i, start=s['in'], end=s['out'], start_next_day=x['in'] >= 1440,
                                       end_next_day=x['out'] is not None and x['out'] >= 1440))
    wd.updated_at = datetime.now(timezone.utc)


def state_out(db, uid) -> dict:
    n = now()
    recs = records_around(db, uid, n)
    ak = jc.active_day(recs, n)
    if ak not in recs: recs[ak] = None
    day = day_out(ak, recs[ak], n, ak)
    return {'server_now': n.isoformat(), 'active_day': ak.isoformat(), 'status': day['status'], 'day': day}


@router.get('/state')
def get_state(user_id: int | None = None, u=Depends(current_user), db: Session = Depends(get_db)):
    return state_out(db, target_user(u, user_id, db))


class PunchIn(BaseModel):
    client_id: str = Field(min_length=8, max_length=64, pattern=r'^[A-Za-z0-9_-]+$')
    kind: Literal['in', 'break', 'out', 'undo']
    at: datetime


@router.post('/punch')
def punch(p: PunchIn, u=Depends(current_user), db: Session = Depends(get_db)):
    """Príchod/prestávka/odchod/zrušenie odchodu s časom z klienta (aj oneskorene z offline fronty)."""
    if db.scalar(select(WorkPunch).where(WorkPunch.user_id == u.id, WorkPunch.client_id == p.client_id)):
        return {'result': 'duplicate', 'duplicate': True, 'state': state_out(db, u.id)}
    if p.at.tzinfo is None: raise HTTPException(422, 'Čas pípnutia musí obsahovať časové pásmo.')
    at = p.at.astimezone(TZ).replace(second=0, microsecond=0)
    n = now()
    if p.at.astimezone(TZ) > n + PUNCH_FUTURE: raise HTTPException(422, 'Čas pípnutia je v budúcnosti. Skontrolujte hodiny zariadenia.')
    if at < n - PUNCH_PAST: raise HTTPException(422, 'Pípnutie je staršie ako 7 dní. Upravte deň ručne v histórii.')
    employee(db, u.id)  # zámok riadku používateľa serializuje súbežné pípnutia
    recs = {d: rec_of(wd) for d, wd in load_days(db, u.id, at.date() - timedelta(days=1), at.date()).items()}
    try:
        res = jc.apply_punch(p.kind, at, recs)
    except jc.PunchConflict as e:
        raise HTTPException(409, str(e))
    if res: store(db, u.id, *res)
    db.add(WorkPunch(user_id=u.id, client_id=p.client_id, kind=p.kind, at=utc(at), result='applied' if res else 'noop'))
    try:
        db.commit()
    except IntegrityError:  # rovnaké client_id prišlo súbežne druhou cestou
        db.rollback()
        return {'result': 'duplicate', 'duplicate': True, 'state': state_out(db, u.id)}
    return {'result': 'applied' if res else 'noop', 'duplicate': False, 'state': state_out(db, u.id)}


@router.get('/days')
def list_days(month: str, user_id: int | None = None, u=Depends(current_user), db: Session = Depends(get_db)):
    uid = target_user(u, user_id, db)
    start, end = month_bounds(month)
    n = now()
    ak = jc.active_day(records_around(db, uid, n), n)
    return [day_out(d, rec_of(wd), n, ak) for d, wd in sorted(load_days(db, uid, start, end).items()) if wd.segments]


@router.get('/days/{day}')
def get_day(day: date, user_id: int | None = None, u=Depends(current_user), db: Session = Depends(get_db)):
    uid = target_user(u, user_id, db)
    n = now()
    ak = jc.active_day(records_around(db, uid, n), n)
    return day_out(day, rec_of(load_days(db, uid, day, day).get(day)), n, ak)


HM_OR_EMPTY = r'^(([01]\d|2[0-3]):[0-5]\d)?$'  # prázdny riadok z editora sa zahodí


class SegmentIn(BaseModel):
    start: str | None = Field(default=None, pattern=HM_OR_EMPTY)
    end: str | None = Field(default=None, pattern=HM_OR_EMPTY)


class DayIn(BaseModel):
    segments: list[SegmentIn] = Field(max_length=50)


@router.put('/days/{day}')
def put_day(day: date, p: DayIn, user_id: int | None = None, u=Depends(current_user), db: Session = Depends(get_db)):
    """Ručná úprava úsekov. Uzavretý posledný úsek znamená odchod na daný deň (ako v pôvodnom denníku)."""
    uid = target_user(u, user_id, db)
    segs = jc.clean([{'in': s.start, 'out': s.end} for s in p.segments])
    err = jc.validate(segs)
    if err: raise HTTPException(422, err)
    employee(db, uid)
    store(db, uid, day, {'segs': segs, 'done': bool(segs and segs[-1]['out'])} if segs else None)
    audit_foreign(db, u, uid, 'journal.day.save', {'date': str(day), 'segments': segs})
    commit(db)
    return get_day(day, uid, u, db)


@router.delete('/days/{day}')
def delete_day(day: date, user_id: int | None = None, u=Depends(current_user), db: Session = Depends(get_db)):
    uid = target_user(u, user_id, db)
    employee(db, uid)
    wd = db.scalar(select(WorkDay).where(WorkDay.user_id == uid, WorkDay.date == day))
    if not wd: raise HTTPException(404, 'Deň nemá zapísanú dochádzku.')
    db.delete(wd)
    audit_foreign(db, u, uid, 'journal.day.delete', str(day))
    commit(db)
    return {'ok': True}


# ---------------------------------------------------------------- zákazníci, projekty, úlohy

def require_admin(u):
    if u.role != 'admin': raise HTTPException(403, 'Deaktivovať a mazať môže iba administrátor.')


def find_customer(db, name: str):
    return db.scalar(select(Customer).where(Customer.name_key == name_key(name)))


def resolve_customer(db, u, customer_id: int | None, customer_name: str | None) -> int | None:
    """Zákazník podľa ID, alebo podľa názvu (nájdi alebo vytvor, ako datalist v pôvodnom denníku)."""
    if customer_id is not None:
        c = db.get(Customer, customer_id)
        if not c: raise HTTPException(422, 'Zákazník neexistuje.')
        return c.id
    name = clean_name(customer_name or '')
    if not name: return None
    if len(name) > 150: raise HTTPException(422, 'Názov zákazníka môže mať najviac 150 znakov.')
    c = find_customer(db, name)
    if not c:
        c = Customer(name=name, name_key=name_key(name), created_by=u.id)
        db.add(c)
        db.flush()
    return c.id


def catalog(db):
    """Malé zdieľané tabuľky načítané naraz (na vkladanie názvov do odpovedí)."""
    customers = {c.id: c for c in db.scalars(select(Customer))}
    projects = {p.id: p for p in db.scalars(select(Project))}
    tasks = {t.id: t for t in db.scalars(select(Task))}
    return customers, projects, tasks


def customer_out(c):
    return {'id': c.id, 'name': c.name, 'active': c.active}


def project_out(p, customers):
    c = customers.get(p.customer_id)
    return {'id': p.id, 'name': p.name, 'customer_id': p.customer_id, 'customer': c.name if c else '', 'active': p.active, 'created_by': p.created_by}


def task_out(t, customers, projects, stats=None):
    p = projects.get(t.project_id)
    cid = p.customer_id if p else t.customer_id
    c = customers.get(cid)
    count, minutes = (stats or {}).get(t.id, (0, 0))
    return {'id': t.id, 'name': t.name, 'project_id': t.project_id, 'project': p.name if p else '', 'customer_id': t.customer_id,
            'customer': c.name if c else '', 'done': t.done, 'active': t.active, 'created_by': t.created_by,
            'activity_count': count, 'minutes': minutes}


class NameIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)

    @field_validator('name')
    @classmethod
    def strip(cls, v):
        v = clean_name(v)
        if not v: raise ValueError('Zadajte názov.')
        return v


class CustomerIn(NameIn):
    name: str = Field(min_length=1, max_length=150)


class CustomerPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    active: bool | None = None


@router.get('/customers')
def customers(include_inactive: bool = False, u=Depends(current_user), db: Session = Depends(get_db)):
    q = select(Customer).order_by(Customer.name)
    if not include_inactive: q = q.where(Customer.active == True)
    return [customer_out(c) for c in db.scalars(q)]


@router.post('/customers')
def add_customer(p: CustomerIn, u=Depends(current_user), db: Session = Depends(get_db)):
    cid = resolve_customer(db, u, None, p.name)
    commit(db)
    return customer_out(db.get(Customer, cid))


@router.patch('/customers/{cid}')
def edit_customer(cid: int, p: CustomerPatch, u=Depends(current_user), db: Session = Depends(get_db)):
    c = db.get(Customer, cid)
    if not c: raise HTTPException(404, 'Zákazník neexistuje.')
    if p.name is not None:
        name = clean_name(p.name)
        if not name: raise HTTPException(422, 'Zadajte názov.')
        other = find_customer(db, name)
        if other and other.id != c.id: raise HTTPException(409, 'Zákazník s týmto názvom už existuje.')
        c.name, c.name_key = name, name_key(name)
    if p.active is not None and p.active != c.active:
        require_admin(u)
        c.active = p.active
        audit(db, u, 'journal.customer.' + ('activate' if p.active else 'deactivate'), c.name)
    commit(db)
    return customer_out(c)


@router.delete('/customers/{cid}')
def delete_customer(cid: int, u=Depends(current_user), db: Session = Depends(get_db)):
    require_admin(u)
    c = db.get(Customer, cid)
    if not c: raise HTTPException(404, 'Zákazník neexistuje.')
    used = any(db.scalar(select(func.count()).select_from(m).where(m.customer_id == cid)) for m in (Project, Task, Todo))
    if used: raise HTTPException(409, 'Zákazník sa používa v projektoch, úlohách alebo to-do. Namiesto zmazania ho deaktivujte.')
    db.delete(c)
    audit(db, u, 'journal.customer.delete', c.name)
    commit(db)
    return {'ok': True}


class ProjectIn(NameIn):
    name: str = Field(min_length=1, max_length=150)
    customer_id: int | None = None
    customer_name: str | None = Field(default=None, max_length=150)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    customer_id: int | None = None
    customer_name: str | None = Field(default=None, max_length=150)
    active: bool | None = None


def project_conflict(db, name, customer_id, exclude=None):
    q = select(Project).where(Project.name_key == name_key(name), Project.customer_id.is_(None) if customer_id is None else Project.customer_id == customer_id)
    if exclude: q = q.where(Project.id != exclude)
    return db.scalar(q)


@router.get('/projects')
def projects(include_inactive: bool = False, u=Depends(current_user), db: Session = Depends(get_db)):
    customers_, projects_, _ = catalog(db)
    rows = sorted(projects_.values(), key=lambda p: p.name.casefold())
    return [project_out(p, customers_) for p in rows if include_inactive or p.active]


@router.post('/projects')
def add_project(p: ProjectIn, u=Depends(current_user), db: Session = Depends(get_db)):
    """Projekt s rovnakým názvom a zákazníkom sa nevytvorí druhýkrát, vráti sa existujúci."""
    cid = resolve_customer(db, u, p.customer_id, p.customer_name)
    obj = project_conflict(db, p.name, cid)
    if not obj:
        obj = Project(name=p.name, name_key=name_key(p.name), customer_id=cid, created_by=u.id)
        db.add(obj)
    commit(db)
    return project_out(obj, catalog(db)[0])


@router.patch('/projects/{pid}')
def edit_project(pid: int, p: ProjectPatch, u=Depends(current_user), db: Session = Depends(get_db)):
    obj = db.get(Project, pid)
    if not obj: raise HTTPException(404, 'Projekt neexistuje.')
    name = clean_name(p.name) if p.name is not None else obj.name
    if not name: raise HTTPException(422, 'Zadajte názov.')
    cid = resolve_customer(db, u, p.customer_id, p.customer_name) if {'customer_id', 'customer_name'} & p.model_fields_set else obj.customer_id
    if project_conflict(db, name, cid, exclude=pid): raise HTTPException(409, 'Projekt s týmto názvom a zákazníkom už existuje.')
    obj.name, obj.name_key, obj.customer_id = name, name_key(name), cid
    if p.active is not None and p.active != obj.active:
        require_admin(u)
        obj.active = p.active
        audit(db, u, 'journal.project.' + ('activate' if p.active else 'deactivate'), obj.name)
    elif u.role == 'admin' and obj.created_by not in (None, u.id):
        audit(db, u, 'journal.project.update', {'id': pid, 'name': name, 'customer_id': cid})
    commit(db)
    return project_out(obj, catalog(db)[0])


@router.delete('/projects/{pid}')
def delete_project(pid: int, u=Depends(current_user), db: Session = Depends(get_db)):
    require_admin(u)
    obj = db.get(Project, pid)
    if not obj: raise HTTPException(404, 'Projekt neexistuje.')
    if any(db.scalar(select(func.count()).select_from(m).where(m.project_id == pid)) for m in (Task, Todo)):
        raise HTTPException(409, 'Projekt má úlohy alebo to-do. Namiesto zmazania ho deaktivujte.')
    db.delete(obj)
    audit(db, u, 'journal.project.delete', obj.name)
    commit(db)
    return {'ok': True}


class TaskIn(NameIn):
    project_id: int | None = None
    customer_id: int | None = None
    customer_name: str | None = Field(default=None, max_length=150)


class TaskPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    project_id: int | None = None
    customer_id: int | None = None
    customer_name: str | None = Field(default=None, max_length=150)
    done: bool | None = None
    active: bool | None = None


def check_project(db, pid):
    if pid is None: return None
    p = db.get(Project, pid)
    if not p: raise HTTPException(422, 'Projekt neexistuje.')
    return p.id


def task_stats(db, uid, task_ids=None):
    """Počet aktivít a odpísané minúty na úlohu pre zobrazeného používateľa."""
    q = select(Activity.task_id, func.count(), func.coalesce(func.sum(Activity.minutes), 0)).where(Activity.user_id == uid).group_by(Activity.task_id)
    if task_ids is not None: q = q.where(Activity.task_id.in_(task_ids))
    return {tid: (cnt, int(mins)) for tid, cnt, mins in db.execute(q)}


@router.get('/tasks')
def tasks(include_inactive: bool = False, user_id: int | None = None, u=Depends(current_user), db: Session = Depends(get_db)):
    uid = target_user(u, user_id, db)
    customers_, projects_, tasks_ = catalog(db)
    stats = task_stats(db, uid)
    rows = sorted(tasks_.values(), key=lambda t: (t.done, t.name.casefold()))
    return [task_out(t, customers_, projects_, stats) for t in rows if include_inactive or t.active]


@router.post('/tasks')
def add_task(p: TaskIn, u=Depends(current_user), db: Session = Depends(get_db)):
    """Úloha patrí projektu, alebo (bez projektu) voliteľne priamo zákazníkovi."""
    pid = check_project(db, p.project_id)
    cid = None if pid else resolve_customer(db, u, p.customer_id, p.customer_name)
    t = Task(name=p.name, project_id=pid, customer_id=cid, created_by=u.id)
    db.add(t)
    commit(db)
    customers_, projects_, _ = catalog(db)
    return task_out(t, customers_, projects_)


@router.patch('/tasks/{tid}')
def edit_task(tid: int, p: TaskPatch, u=Depends(current_user), db: Session = Depends(get_db)):
    t = db.get(Task, tid)
    if not t: raise HTTPException(404, 'Úloha neexistuje.')
    fields = p.model_fields_set
    if p.name is not None:
        name = clean_name(p.name)
        if not name: raise HTTPException(422, 'Zadajte názov.')
        t.name = name
    if p.project_id is not None:
        t.project_id, t.customer_id = check_project(db, p.project_id), None
    elif fields & {'project_id', 'customer_id', 'customer_name'}:
        t.project_id, t.customer_id = None, resolve_customer(db, u, p.customer_id, p.customer_name)
    if p.done is not None: t.done = p.done
    if p.active is not None and p.active != t.active:
        require_admin(u)
        t.active = p.active
        audit(db, u, 'journal.task.' + ('activate' if p.active else 'deactivate'), t.name)
    elif u.role == 'admin' and t.created_by not in (None, u.id):
        audit(db, u, 'journal.task.update', {'id': tid, **p.model_dump(exclude_unset=True)})
    commit(db)
    customers_, projects_, _ = catalog(db)
    return task_out(t, customers_, projects_, task_stats(db, u.id, [t.id]))


@router.delete('/tasks/{tid}')
def delete_task(tid: int, u=Depends(current_user), db: Session = Depends(get_db)):
    require_admin(u)
    t = db.get(Task, tid)
    if not t: raise HTTPException(404, 'Úloha neexistuje.')
    if db.scalar(select(func.count()).select_from(Activity).where(Activity.task_id == tid)):
        raise HTTPException(409, 'Na úlohu sú zapísané aktivity. Namiesto zmazania ju deaktivujte.')
    db.delete(t)
    audit(db, u, 'journal.task.delete', t.name)
    commit(db)
    return {'ok': True}


# ---------------------------------------------------------------- aktivity

class ActivityIn(BaseModel):
    task_id: int
    date: date
    start: str | None = Field(default=None, pattern=HM)
    end: str | None = Field(default=None, pattern=HM)
    text: str = Field(min_length=1, max_length=5000)

    @field_validator('text')
    @classmethod
    def strip(cls, v):
        v = v.replace('\x00', '').strip()   # PostgreSQL neprijme NUL v texte
        if not v: raise ValueError('Napíšte, čo ste robili.')
        return v


def at_local(day: date, hm: str) -> datetime:
    h, m = hm.split(':')
    return datetime.combine(day, time(int(h), int(m)), tzinfo=TZ)


def activity_span(day: date, start: str | None, end: str | None, keep: datetime | None = None):
    """saveActivity(): bez „od“ = teraz (dnes) alebo aktuálny čas v zvolený deň; „do“ ≤ „od“ = nasledujúci deň."""
    if end and not start: raise HTTPException(422, 'Zadajte aj čas „od“.')
    n = now()
    if start: s = at_local(day, start)
    elif keep is not None: s = at_local(day, f'{keep.hour:02d}:{keep.minute:02d}')
    elif day == n.date(): s = n.replace(microsecond=0)
    else: s = at_local(day, f'{n.hour:02d}:{n.minute:02d}')
    e = minutes = None
    if start and end:
        e = at_local(day, end)
        if e <= s: e = at_local(day + timedelta(days=1), end)
        minutes = round((e - s).total_seconds() / 60)
    return s, e, minutes


def activity_out(a, customers, projects, tasks_):
    t = tasks_.get(a.task_id)
    p = projects.get(t.project_id) if t else None
    c = customers.get(p.customer_id if p else (t.customer_id if t else None))
    s, e = local(a.start_at), local(a.end_at)
    return {'id': a.id, 'user_id': a.user_id, 'task_id': a.task_id, 'task': t.name if t else '', 'task_done': bool(t and t.done),
            'project_id': p.id if p else None, 'project': p.name if p else '', 'customer': c.name if c else '',
            'date': s.date().isoformat(), 'start': s.strftime('%H:%M'), 'end': e.strftime('%H:%M') if e else None,
            'end_next_day': bool(e and e.date() != s.date()), 'minutes': a.minutes, 'text': a.text,
            'start_at': s.isoformat(), 'end_at': e.isoformat() if e else None}


def usable_task(db, tid, current=None):
    t = db.get(Task, tid)
    if not t or (not t.active and tid != current): raise HTTPException(422, 'Vyberte aktívnu úlohu.')
    return t


@router.get('/activities')
def activities(start: date | None = None, end: date | None = None, task_id: int | None = None, q: str | None = None,
               user_id: int | None = None, limit: int = 500, u=Depends(current_user), db: Session = Depends(get_db)):
    uid = target_user(u, user_id, db)
    query = select(Activity).where(Activity.user_id == uid).order_by(Activity.start_at.desc(), Activity.id.desc()).limit(max(1, min(limit, 5000)))
    if start: query = query.where(Activity.start_at >= utc(at_local(start, '00:00')))
    if end: query = query.where(Activity.start_at < utc(at_local(end + timedelta(days=1), '00:00')))
    if task_id is not None: query = query.where(Activity.task_id == task_id)
    if q and q.strip(): query = query.where(func.lower(Activity.text).contains(q.strip().lower()))
    cat = catalog(db)
    return [activity_out(a, *cat) for a in db.scalars(query)]


@router.post('/activities')
def add_activity(p: ActivityIn, user_id: int | None = None, u=Depends(current_user), db: Session = Depends(get_db)):
    uid = target_user(u, user_id, db)
    usable_task(db, p.task_id)
    s, e, minutes = activity_span(p.date, p.start, p.end)
    a = Activity(user_id=uid, task_id=p.task_id, start_at=utc(s), end_at=utc(e), minutes=minutes, text=p.text)
    db.add(a)
    audit_foreign(db, u, uid, 'journal.activity.create', {'task_id': p.task_id, 'date': str(p.date)})
    commit(db)
    return activity_out(a, *catalog(db))


def own_activity(db, u, aid):
    a = db.get(Activity, aid)
    if not a: raise HTTPException(404, 'Aktivita neexistuje.')
    owner(u, a.user_id)
    return a


@router.put('/activities/{aid}')
def edit_activity(aid: int, p: ActivityIn, u=Depends(current_user), db: Session = Depends(get_db)):
    """Úprava ako v editore pôvodného denníka; bez „od“ sa zachová pôvodný čas začiatku."""
    a = own_activity(db, u, aid)
    usable_task(db, p.task_id, a.task_id)
    a.task_id = p.task_id
    s, e, a.minutes = activity_span(p.date, p.start, p.end, keep=local(a.start_at))
    a.start_at, a.end_at = utc(s), utc(e)
    a.text = p.text
    audit_foreign(db, u, a.user_id, 'journal.activity.update', {'id': aid})
    commit(db)
    return activity_out(a, *catalog(db))


@router.delete('/activities/{aid}')
def delete_activity(aid: int, u=Depends(current_user), db: Session = Depends(get_db)):
    a = own_activity(db, u, aid)
    db.delete(a)
    audit_foreign(db, u, a.user_id, 'journal.activity.delete', {'id': aid})
    commit(db)
    return {'ok': True}


# ---------------------------------------------------------------- to-do

class TodoIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    due: date | None = None
    priority: int = Field(default=0, ge=0, le=2)
    project_id: int | None = None
    customer_id: int | None = None
    customer_name: str | None = Field(default=None, max_length=150)

    @field_validator('text')
    @classmethod
    def strip(cls, v):
        v = v.replace('\x00', '').strip()
        if not v: raise ValueError('Napíšte text to-do.')
        return v


class TodoPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=500)
    due: date | None = None
    priority: int | None = Field(default=None, ge=0, le=2)
    project_id: int | None = None
    customer_id: int | None = None
    customer_name: str | None = Field(default=None, max_length=150)
    done: bool | None = None


def todo_out(t, customers, projects):
    p = projects.get(t.project_id)
    c = customers.get(t.customer_id)
    done_at = local(t.done_at)
    return {'id': t.id, 'user_id': t.user_id, 'text': t.text, 'due': t.due.isoformat() if t.due else None, 'priority': t.priority,
            'project_id': t.project_id, 'project': p.name if p else '', 'project_customer': (customers[p.customer_id].name if p and p.customer_id in customers else ''),
            'customer_id': t.customer_id, 'customer': c.name if c else '', 'done': done_at is not None,
            'done_at': done_at.isoformat() if done_at else None, 'created_at': local(t.created_at).isoformat()}


def assign_todo(db, u, t, p, fields):
    if p.project_id is not None:
        t.project_id, t.customer_id = check_project(db, p.project_id), None
    elif fields & {'project_id', 'customer_id', 'customer_name'}:
        t.project_id, t.customer_id = None, resolve_customer(db, u, p.customer_id, p.customer_name)


@router.get('/todos')
def todos(user_id: int | None = None, u=Depends(current_user), db: Session = Depends(get_db)):
    uid = target_user(u, user_id, db)
    customers_, projects_, _ = catalog(db)
    return [todo_out(t, customers_, projects_) for t in db.scalars(select(Todo).where(Todo.user_id == uid).order_by(Todo.created_at, Todo.id))]


@router.post('/todos')
def add_todo(p: TodoIn, user_id: int | None = None, u=Depends(current_user), db: Session = Depends(get_db)):
    uid = target_user(u, user_id, db)
    t = Todo(user_id=uid, text=p.text, due=p.due, priority=p.priority)
    assign_todo(db, u, t, p, p.model_fields_set)
    db.add(t)
    audit_foreign(db, u, uid, 'journal.todo.create', p.text)
    commit(db)
    return todo_out(t, *catalog(db)[:2])


def own_todo(db, u, tid):
    t = db.get(Todo, tid)
    if not t: raise HTTPException(404, 'To-do neexistuje.')
    owner(u, t.user_id)
    return t


@router.patch('/todos/{tid}')
def edit_todo(tid: int, p: TodoPatch, u=Depends(current_user), db: Session = Depends(get_db)):
    t = own_todo(db, u, tid)
    fields = p.model_fields_set
    if p.text is not None:
        if not p.text.replace('\x00', '').strip(): raise HTTPException(422, 'Napíšte text to-do.')
        t.text = p.text.replace('\x00', '').strip()
    if 'due' in fields: t.due = p.due
    if p.priority is not None: t.priority = p.priority
    assign_todo(db, u, t, p, fields)
    if p.done is not None and p.done != (t.done_at is not None):
        t.done_at = utc(now()) if p.done else None
    audit_foreign(db, u, t.user_id, 'journal.todo.update', {'id': tid})
    commit(db)
    return todo_out(t, *catalog(db)[:2])


@router.delete('/todos/{tid}')
def delete_todo(tid: int, u=Depends(current_user), db: Session = Depends(get_db)):
    t = own_todo(db, u, tid)
    db.delete(t)
    audit_foreign(db, u, t.user_id, 'journal.todo.delete', {'id': tid})
    commit(db)
    return {'ok': True}


@router.post('/todos/clear-done')
def clear_done_todos(user_id: int | None = None, u=Depends(current_user), db: Session = Depends(get_db)):
    uid = target_user(u, user_id, db)
    n = db.execute(delete(Todo).where(Todo.user_id == uid, Todo.done_at.is_not(None))).rowcount
    audit_foreign(db, u, uid, 'journal.todo.clear_done', n)
    commit(db)
    return {'deleted': n}


# ---------------------------------------------------------------- import zo starého denníka

MAX_IMPORT_BYTES = 5 * 1024 * 1024


def import_size_limit(request: Request):
    try:
        size = int(request.headers.get('content-length') or 0)
    except ValueError:
        size = 0
    if size > MAX_IMPORT_BYTES: raise HTTPException(413, 'Súbor je väčší ako 5 MB.')


@router.post('/import', dependencies=[Depends(import_size_limit)])
def import_legacy(payload: dict = Body(...), dry_run: bool = True, u=Depends(current_user), db: Session = Depends(get_db)):
    """Import JSON zálohy zo starého denníka pre prihláseného používateľa. dry_run=true (predvolené) = iba náhľad."""
    from .journal_import import run_import
    employee(db, u.id)  # zámok používateľa: dva súbežné importy sa neprekryjú
    return run_import(db, u, payload, dry_run)

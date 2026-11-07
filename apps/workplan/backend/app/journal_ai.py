"""AI doplnenie dňa v Denníku: z voľného textu návrh aktivít (a nových úloh) do odpracovaného času.

Postup: GET /ai/day ukáže odpracovaný, zapísaný a voľný čas; POST /ai/propose pošle text (a rozpracovaný návrh)
Gemini a vráti skontrolované riadky s upozorneniami; POST /ai/apply zapíše potvrdené riadky v jednej transakcii.
Gemini nikdy nezapisuje. Časy sú minúty od polnoci daného dňa (≥ 1440 = nasledujúci deň).
"""
from datetime import date, timedelta
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import get_db
from .models import Activity, Task, WorkDay
from .security import current_user
from .common import commit
from . import gemini, journal as J
from . import journal_calc as jc

router = APIRouter(prefix='/api/journal/ai', tags=['journal-ai'])
HM = r'^([01]\d|2[0-3]):[0-5]\d$'
MAX_ITEMS = 40


def hm(m: int) -> str:
    return f'{(m // 60) % 24:02d}:{m % 60:02d}'


def to_min(s: str) -> int:
    h, m = s.split(':')
    return int(h) * 60 + int(m)


def span(start: str, end: str) -> tuple[int, int]:
    """Ako activity_span: „do“ ≤ „od“ znamená nasledujúci deň."""
    a, b = to_min(start), to_min(end)
    return a, b if b > a else b + 1440


def worked(db, uid: int, day: date) -> list[tuple[int, int]]:
    """Odpracované úseky dňa v minútach; bežiaci úsek končí teraz (ak je to dnešok/včerajšok)."""
    wd = db.scalar(select(WorkDay).where(WorkDay.user_id == uid, WorkDay.date == day))
    rec = J.rec_of(wd)
    if not rec: return []
    out = []
    n = J.now()
    for s in jc.unwrap(rec['segs']):
        if s['in'] is None: continue
        end = s['out']
        if end is None:
            elapsed = (n - J.at_local(day, '00:00')).total_seconds() // 60
            if 0 < elapsed < 2880: end = int(elapsed)
        if end is not None and end > s['in']: out.append((s['in'], end))
    return out


def logged(db, uid: int, day: date) -> list[dict]:
    """Aktivity s trvaním, ktoré zasahujú do dňa (aj tie z predošlého dňa cez polnoc)."""
    base = J.at_local(day, '00:00')
    q = select(Activity).where(Activity.user_id == uid, Activity.end_at.is_not(None),
                               Activity.start_at < J.utc(base + timedelta(days=2)),
                               Activity.end_at > J.utc(base - timedelta(days=1)))
    _, _, tasks = J.catalog(db)
    out = []
    for a in db.scalars(q.order_by(Activity.start_at)):
        s = int((J.local(a.start_at) - base).total_seconds() // 60)
        e = int((J.local(a.end_at) - base).total_seconds() // 60)
        if e <= 0 or s >= 2880: continue
        t = tasks.get(a.task_id)
        out.append({'start': s, 'end': e, 'task': t.name if t else '', 'text': a.text[:200]})
    return out


def subtract(ranges, busy):
    free = []
    for s, e in ranges:
        cur = s
        for bs, be in sorted(busy):
            if be <= cur or bs >= e: continue
            if bs > cur: free.append((cur, bs))
            cur = max(cur, be)
        if cur < e: free.append((cur, e))
    return free


def label(r: tuple[int, int]) -> dict:
    return {'start': hm(r[0]), 'end': hm(r[1]), 'next_day': r[1] > 1440 or r[0] >= 1440, 'minutes': r[1] - r[0]}


def day_info(db, uid, day):
    w = worked(db, uid, day)
    lg = logged(db, uid, day)
    free = subtract(w, [(x['start'], x['end']) for x in lg])
    return {'date': day.isoformat(), 'configured': gemini.configured(),
            'worked': [label(r) for r in w], 'worked_min': sum(e - s for s, e in w),
            'logged': [{**label((x['start'], x['end'])), 'task': x['task'], 'text': x['text']} for x in lg],
            'logged_min': sum(min(x['end'], 2880) - max(x['start'], 0) for x in lg),
            'free': [label(r) for r in free], 'free_min': sum(e - s for s, e in free)}, w, lg


@router.get('/day')
def ai_day(date: date, user_id: int | None = None, u=Depends(current_user), db: Session = Depends(get_db)):
    uid = J.target_user(u, user_id, db)
    return day_info(db, uid, date)[0]


# ---------------------------------------------------------------- návrh

class ItemIn(BaseModel):
    start: str = Field(pattern=HM)
    end: str = Field(pattern=HM)
    task_id: int | None = None
    new_task: str | None = Field(default=None, max_length=200)
    project_id: int | None = None
    customer: str | None = Field(default=None, max_length=150)
    text: str = Field(default='', max_length=5000)

    @field_validator('new_task', 'customer')
    @classmethod
    def v_name(cls, v): return J.clean_name(v) or None if v is not None else None


class Msg(BaseModel):
    role: str = Field(pattern=r'^(user|model)$')
    text: str = Field(max_length=4000)


class ProposeIn(BaseModel):
    date: date
    message: str = Field(min_length=1, max_length=4000)
    history: list[Msg] = Field(default_factory=list, max_length=20)
    draft: list[ItemIn] = Field(default_factory=list, max_length=MAX_ITEMS)


def check_items(db, items: list[ItemIn], worked_: list, logged_: list) -> list[dict]:
    """Doplní názvy a upozornenia; chyby (errors) bránia zápisu, varovania (warnings) nie."""
    customers, projects, tasks = J.catalog(db)
    spans = [span(i.start, i.end) for i in items]
    out = []
    for n, (i, (s, e)) in enumerate(zip(items, spans)):
        errors, warnings = [], []
        t = tasks.get(i.task_id) if i.task_id else None
        if i.task_id and (not t or not t.active): errors.append('Úloha neexistuje alebo nie je aktívna.')
        if not i.task_id and not i.new_task: errors.append('Vyberte úlohu alebo zadajte novú.')
        if i.project_id and i.project_id not in projects: errors.append('Projekt neexistuje.')
        if not i.text.strip(): errors.append('Doplňte popis.')
        if any(a < e and s < b for k, (a, b) in enumerate(spans) if k != n): errors.append('Prekrýva sa s iným riadkom návrhu.')
        if any(x['start'] < e and s < x['end'] for x in logged_): errors.append('Prekrýva sa s už zapísanou aktivitou.')
        if worked_ and not any(a <= s and e <= b for a, b in worked_): warnings.append('Mimo odpracovaného času (pípnutí).')
        if e - s > 16 * 60: warnings.append('Aktivita trvá viac ako 16 hodín.')
        p = projects.get(t.project_id if t else i.project_id)
        c = customers.get(t.customer_id) if t and t.customer_id else None
        out.append({**i.model_dump(), 'next_day': e > 1440, 'minutes': e - s,
                    'task': t.name if t else '', 'task_done': bool(t and t.done),
                    'project': p.name if p else '', 'customer_name': c.name if c else (i.customer or ''),
                    'errors': errors, 'warnings': warnings})
    return out


def catalog_for_ai(db):
    customers, projects, tasks = J.catalog(db)
    return {'projekty': [{'id': p.id, 'nazov': p.name, 'zakaznik': customers[p.customer_id].name if p.customer_id in customers else None}
                         for p in projects.values() if p.active],
            'ulohy': [{'id': t.id, 'nazov': t.name, 'projekt_id': t.project_id,
                       'zakaznik': customers[t.customer_id].name if t.customer_id in customers else None, 'hotova': t.done}
                      for t in tasks.values() if t.active]}


@router.post('/propose')
def propose(p: ProposeIn, user_id: int | None = None, u=Depends(current_user), db: Session = Depends(get_db)):
    uid = J.target_user(u, user_id, db)
    info, w, lg = day_info(db, uid, p.date)
    context = {'den': p.date.isoformat(), 'den_v_tyzdni': ['pondelok', 'utorok', 'streda', 'štvrtok', 'piatok', 'sobota', 'nedeľa'][p.date.weekday()],
               'odpracovane': info['worked'], 'uz_zapisane_aktivity': info['logged'], 'volny_cas': info['free'],
               **catalog_for_ai(db), 'rozpracovany_navrh': [i.model_dump() for i in p.draft]}
    try:
        r = gemini.propose(context, [m.model_dump() for m in p.history], p.message)
    except gemini.AIError as e:
        raise HTTPException(502, str(e))
    items = []
    for x in r.items[:MAX_ITEMS]:   # nezmyselné časy z modelu zahodíme, zvyšok skontroluje check_items
        try: items.append(ItemIn(**x.model_dump()))
        except ValueError: continue
    return {'reply': r.reply, 'items': check_items(db, items, w, [{'start': x['start'], 'end': x['end']} for x in lg]), 'day': info}


class CheckIn(BaseModel):
    date: date
    items: list[ItemIn] = Field(max_length=MAX_ITEMS)


@router.post('/check')
def check(p: CheckIn, user_id: int | None = None, u=Depends(current_user), db: Session = Depends(get_db)):
    """Kontrola ručne upravenej tabuľky (bez AI)."""
    uid = J.target_user(u, user_id, db)
    _, w, lg = day_info(db, uid, p.date)
    return check_items(db, p.items, w, lg)


@router.post('/apply')
def apply(p: CheckIn, user_id: int | None = None, u=Depends(current_user), db: Session = Depends(get_db)):
    uid = J.target_user(u, user_id, db)
    if not p.items: raise HTTPException(422, 'Návrh je prázdny.')
    _, w, lg = day_info(db, uid, p.date)
    checked = check_items(db, p.items, w, lg)
    bad = [f"{c['start']}–{c['end']}: {'; '.join(c['errors'])}" for c in checked if c['errors']]
    if bad: raise HTTPException(422, 'Návrh obsahuje chyby. ' + ' '.join(bad))
    customers, projects, tasks = J.catalog(db)
    made, new_tasks, created = [], {}, []
    for i in p.items:
        tid = i.task_id
        if not tid:
            pid = i.project_id
            cid = None if pid else J.resolve_customer(db, u, None, i.customer)
            key = (i.new_task.casefold(), pid, cid)
            if key not in new_tasks:
                same = next((t for t in tasks.values() if t.active and t.name.casefold() == key[0] and t.project_id == pid and t.customer_id == cid), None)
                if same: new_tasks[key] = same.id
                else:
                    t = Task(name=i.new_task, project_id=pid, customer_id=cid, created_by=u.id)
                    db.add(t); db.flush(); tasks[t.id] = t
                    new_tasks[key] = t.id
                    created.append(t.name)
            tid = new_tasks[key]
        s, e, minutes = J.activity_span(p.date, i.start, i.end)
        a = Activity(user_id=uid, task_id=tid, start_at=J.utc(s), end_at=J.utc(e), minutes=minutes, text=i.text.replace('\x00', '').strip())
        db.add(a); made.append(a)
    J.audit_foreign(db, u, uid, 'journal.activity.create', {'ai': True, 'date': str(p.date), 'count': len(made)})
    commit(db)
    return {'created': len(made), 'new_tasks': created}

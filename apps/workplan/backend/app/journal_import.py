"""Import dát zo starého samostatného Pracovného denníka (localStorage) pre prihláseného používateľa.

Prijíma:
- zálohu z tlačidla „Exportovať zálohu (JSON)“: {app: "pracovny-dennik", version, exportedAt, data: state},
- holý `state` (v3/v4): {presence, attendance, projects, tasks, activities, todos},
- holý formát v2 (`pracovny_dennik_v2`): {tasks: [{name, project, customer, done, activities: [...]}], presence}.
Staršie formáty prevádza rovnako ako pôvodný denník (migrateV2, migrateSegments, toSegRec, normalize).

Idempotencia: pôvodné ID sa ukladajú do journal_import_map (user, druh, legacy_id). Zákazníci sa párujú
podľa názvu (trim, bez ohľadu na veľkosť písmen), projekty podľa (názov, zákazník), existujúci deň dochádzky
sa nikdy neprepíše. Opakovaný import teda nič nezduplikuje.
"""
import hashlib
import json
from datetime import date, datetime, timezone
from fastapi import HTTPException
from sqlalchemy import select, func
from .models import Customer, Project, Task, WorkDay, Activity, Card, BoardColumn, ImportMap, name_key
from .common import TZ, audit
from . import journal_calc as jc
from .journal import resolve_customer, project_conflict, store, utc, now, clean_name

KINDS = ('days', 'segments', 'customers', 'projects', 'tasks', 'activities', 'todos')
SAMPLE_ACTIVITY = 'Ukážková aktivita — projekt aj úlohu môžeš zmazať.'  # z blankState() pôvodného denníka
MAX_WARNINGS = 50


def legacy_key(value, fallback) -> str:
    """Pôvodné ID; ak chýba alebo je príliš dlhé, deterministický hash obsahu."""
    if isinstance(value, (str, int)) and str(value).strip() and len(str(value)) <= 100 and '\x00' not in str(value): return str(value)
    return 'h:' + hashlib.sha1(json.dumps(fallback, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def from_ms(ms):
    """Epoch v ms (Date.now() v prehliadači) → aware datetime; neplatné → None."""
    if isinstance(ms, bool) or not isinstance(ms, (int, float)): return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def text_of(v, limit) -> str:
    return (v if isinstance(v, str) else '').replace('\x00', '').strip()[:limit]


# ---------------------------------------------------------------- staré formáty (port z HTML)

def to_seg_rec(r):
    """toSegRec(): {segs, done} alebo starý {arrival, breakStart, breakEnd, departure}."""
    if not isinstance(r, dict): return None
    if isinstance(r.get('segs'), list):
        segs = [{'in': x.get('in') or None, 'out': x.get('out') or None} for x in r['segs'] if isinstance(x, dict) and (x.get('in') or x.get('out'))]
        return {'segs': segs, 'done': bool(r.get('done'))} if segs else None
    if not r.get('arrival'): return None
    if r.get('breakStart'):
        segs = [{'in': r['arrival'], 'out': r['breakStart']}]
        if r.get('breakEnd'): segs.append({'in': r['breakEnd'], 'out': r.get('departure') or None})
    else:
        segs = [{'in': r['arrival'], 'out': r.get('departure') or None}]
    return {'segs': segs, 'done': bool(r.get('departure'))}


def migrate_segments(presence, n: datetime):
    """migrateSegments(): najstarší formát presence.segments [{start, end}] v ms → staré záznamy dní."""
    def lt(ms): return from_ms(ms).astimezone(TZ)
    def hm(ms): d = lt(ms); return f'{d.hour:02d}:{d.minute:02d}'
    by_day = {}
    for sg in (presence or {}).get('segments') or []:
        if isinstance(sg, dict) and from_ms(sg.get('start')) and from_ms(sg.get('end')):
            by_day.setdefault(lt(sg['start']).date(), []).append(sg)
    att = {}
    for k, sg in by_day.items():
        sg.sort(key=lambda x: x['start'])
        r = {'arrival': hm(sg[0]['start']), 'breakStart': None, 'breakEnd': None, 'departure': hm(sg[-1]['end'])}
        if len(sg) >= 2: r['breakStart'], r['breakEnd'] = hm(sg[0]['end']), hm(sg[1]['start'])
        att[k.isoformat()] = r
    today = n.date().isoformat()
    status, since = (presence or {}).get('status'), (presence or {}).get('since')
    if status == 'in' and from_ms(since) and lt(since).date().isoformat() == today:
        r = att.get(today)
        if not r: att[today] = {'arrival': hm(since), 'breakStart': None, 'breakEnd': None, 'departure': None}
        else:
            if not r['breakStart']: r['breakStart'], r['breakEnd'] = r['departure'], hm(since)
            r['departure'] = None
    elif status == 'break' and today in att:
        r = att[today]
        if not r['breakStart']: r['breakStart'], r['breakEnd'] = r['departure'], None
        r['departure'] = None
    return att


def migrate_v2(o):
    """migrateV2(): úlohy s textovým projektom/zákazníkom a vnorenými aktivitami → projekty/úlohy/aktivity."""
    projects, tasks, activities = [], [], []
    for t in o.get('tasks') or []:
        if not isinstance(t, dict): continue
        cust, proj = text_of(t.get('customer'), 150), text_of(t.get('project'), 150)
        pid = None
        if proj:
            p = next((p for p in projects if p['name'] == proj and p['customer'] == cust), None)
            if not p:
                p = {'id': legacy_key(None, ['v2project', proj, cust]), 'name': proj, 'customer': cust}
                projects.append(p)
            pid = p['id']
        tid = legacy_key(t.get('id'), ['v2task', t.get('name'), proj, cust])
        tasks.append({'id': tid, 'name': t.get('name'), 'projectId': pid, 'customer': '' if pid else cust, 'done': bool(t.get('done'))})
        for a in t.get('activities') or []:
            if isinstance(a, dict):
                activities.append({'id': a.get('id'), 'ts': a.get('ts'), 'taskId': tid, 'text': a.get('text'),
                                   'minutes': a.get('minutes') if isinstance(a.get('minutes'), (int, float)) and not isinstance(a.get('minutes'), bool) else None})
    return {'presence': {'status': 'out', 'since': None, 'segments': (o.get('presence') or {}).get('segments') or []},
            'projects': projects, 'tasks': tasks, 'activities': activities}


def normalize(payload, n: datetime) -> dict:
    """Rozbalí zálohu, rozpozná formát a vráti stav v3 s attendance {YYYY-MM-DD: {segs, done}}."""
    if not isinstance(payload, dict): raise HTTPException(422, 'Súbor nevyzerá ako záloha Pracovného denníka.')
    s = payload['data'] if isinstance(payload.get('data'), dict) else payload
    tasks = s.get('tasks')
    is_v2 = isinstance(tasks, list) and not isinstance(s.get('projects'), list) and any(isinstance(t, dict) and ('activities' in t or 'project' in t) for t in tasks)
    if is_v2: s = migrate_v2(s)
    elif not (isinstance(tasks, list) and isinstance(s.get('activities'), list)):
        raise HTTPException(422, 'Súbor nevyzerá ako záloha Pracovného denníka (chýbajú úlohy a aktivity).')
    presence = s.get('presence') if isinstance(s.get('presence'), dict) else {'status': 'out', 'since': None, 'segments': []}
    attendance = s.get('attendance') if isinstance(s.get('attendance'), dict) else migrate_segments(presence, n)
    days = {}
    for k, r in attendance.items():
        rec = to_seg_rec(r)
        if rec: days[k] = rec
    # strop veľkosti: bez neho by jeden súbor zamestnal server na minúty a zaplnil spoločný katalóg
    for key, limit, label in (('projects', 2000, 'projektov'), ('tasks', 5000, 'úloh'), ('todos', 5000, 'to-do položiek'), ('activities', 30000, 'aktivít')):
        if isinstance(s.get(key), list) and len(s[key]) > limit: raise HTTPException(422, f'Záloha obsahuje priveľa {label} (najviac {limit}).')
    if len(days) > 4000: raise HTTPException(422, 'Záloha obsahuje priveľa dní dochádzky.')
    return {'attendance': days, 'projects': [p for p in s.get('projects') or [] if isinstance(p, dict)],
            'tasks': [t for t in s.get('tasks') or [] if isinstance(t, dict)],
            'activities': [a for a in s.get('activities') or [] if isinstance(a, dict)],
            'todos': [t for t in (s.get('todos') if isinstance(s.get('todos'), list) else []) if isinstance(t, dict)]}


# ---------------------------------------------------------------- zápis

class Importer:
    def __init__(self, db, u):
        self.db, self.u = db, u
        self.created = dict.fromkeys(KINDS, 0)
        self.skipped = dict.fromkeys(KINDS, 0)
        self.warnings = []
        self.maps = {(m.kind, m.legacy_id): m.target_id for m in db.scalars(select(ImportMap).where(ImportMap.user_id == u.id))}
        self.customer_ids = set()

    def warn(self, msg):
        if len(self.warnings) < MAX_WARNINGS: self.warnings.append(msg)

    def mapped(self, kind, legacy, model):
        tid = self.maps.get((kind, legacy))
        return tid if tid is not None and self.db.get(model, tid) else None

    def remember(self, kind, legacy, target):
        if (kind, legacy) in self.maps:
            self.db.query(ImportMap).filter_by(user_id=self.u.id, kind=kind, legacy_id=legacy).update({'target_id': target})
        else:
            self.db.add(ImportMap(user_id=self.u.id, kind=kind, legacy_id=legacy, target_id=target))
        self.maps[(kind, legacy)] = target

    def customer(self, name) -> int | None:
        name = clean_name(text_of(name, 150))
        if not name: return None
        existed = self.db.scalar(select(Customer).where(Customer.name_key == name_key(name)))
        cid = resolve_customer(self.db, self.u, None, name)
        if cid not in self.customer_ids:
            self.customer_ids.add(cid)
            if existed: self.skipped['customers'] += 1
            else: self.created['customers'] += 1
        return cid

    def projects(self, items) -> dict:
        ids = {}
        for p in items:
            name = clean_name(text_of(p.get('name'), 150))
            legacy = legacy_key(p.get('id'), ['project', p.get('name'), p.get('customer')])
            if not name:
                self.warn('Projekt bez názvu bol vynechaný.'); continue
            target = self.mapped('project', legacy, Project)
            if target is None:
                cid = self.customer(p.get('customer'))
                obj = project_conflict(self.db, name, cid)
                if obj: self.skipped['projects'] += 1
                else:
                    obj = Project(name=name, name_key=name_key(name), customer_id=cid, created_by=self.u.id)
                    self.db.add(obj); self.db.flush()
                    self.created['projects'] += 1
                target = obj.id
                self.remember('project', legacy, target)
            else:
                self.skipped['projects'] += 1
            ids[p.get('id')] = target
        return ids

    def tasks(self, items, project_ids, sample_tasks) -> dict:
        ids = {}
        for t in items:
            legacy = legacy_key(t.get('id'), ['task', t.get('name'), t.get('projectId'), t.get('customer')])
            if t.get('id') in sample_tasks:
                self.warn('Ukážková úloha „Príklad: prevzatie serverov“ bola vynechaná.'); continue
            name = clean_name(text_of(t.get('name'), 200))
            if not name:
                self.warn('Úloha bez názvu bola vynechaná.'); continue
            target = self.mapped('task', legacy, Task)
            if target is None:
                pid = project_ids.get(t.get('projectId')) if t.get('projectId') else None
                if t.get('projectId') and pid is None: self.warn(f'Úloha „{name}“ odkazovala na neexistujúci projekt; importuje sa bez projektu.')
                cid = None if pid else self.customer(t.get('customer'))
                # zdieľaný katalóg: rovnaká úloha v tom istom projekte/zákazníkovi sa nevytvára druhýkrát
                same = self.db.scalars(select(Task).where(Task.project_id.is_(None) if pid is None else Task.project_id == pid,
                                                          Task.customer_id.is_(None) if cid is None else Task.customer_id == cid)).all()
                obj = next((x for x in same if name_key(x.name) == name_key(name)), None)
                if obj: self.skipped['tasks'] += 1
                else:
                    obj = Task(name=name, project_id=pid, customer_id=cid, done=bool(t.get('done')), created_by=self.u.id)
                    self.db.add(obj); self.db.flush()
                    self.created['tasks'] += 1
                target = obj.id
                self.remember('task', legacy, target)
            else:
                self.skipped['tasks'] += 1
            ids[t.get('id')] = target
        return ids

    def activities(self, items, task_ids):
        for a in items:
            text = text_of(a.get('text'), 5000)
            if text == SAMPLE_ACTIVITY: continue
            legacy = legacy_key(a.get('id'), ['activity', a.get('ts'), a.get('taskId'), a.get('text')])
            if self.mapped('activity', legacy, Activity):
                self.skipped['activities'] += 1; continue
            start = from_ms(a.get('ts'))
            if not start:
                self.warn('Aktivita bez platného času bola vynechaná.'); continue
            end = from_ms(a.get('end'))
            if end and end <= start:
                end = None
            minutes = a.get('minutes')
            minutes = round(minutes) if isinstance(minutes, (int, float)) and not isinstance(minutes, bool) and minutes >= 0 else None
            tid = task_ids.get(a.get('taskId'))
            if tid is None: self.warn(f'Aktivita z {start.astimezone(TZ):%d.%m.%Y} nemá existujúcu úlohu; importuje sa bez úlohy.')
            obj = Activity(user_id=self.u.id, task_id=tid, start_at=utc(start), end_at=utc(end), minutes=minutes, text=text or '(bez textu)')
            self.db.add(obj); self.db.flush()
            self.remember('activity', legacy, obj.id)
            self.created['activities'] += 1

    def todos(self, items, project_ids):
        """Staré to-do → karty na prvej vlastnej nástenke (otvorené do prvého stĺpca, hotové do stĺpca Hotovo)."""
        from .boards import ensure_default_board
        board = cols = None
        positions = {}
        def next_pos(cid):
            if cid not in positions:
                m = self.db.scalar(select(func.max(Card.position)).where(Card.column_id == cid))
                positions[cid] = -1 if m is None else m
            positions[cid] += 1
            return positions[cid]
        for t in items:
            text = text_of(t.get('text'), 500)
            if not text: continue
            legacy = legacy_key(t.get('id'), ['todo', t.get('text'), t.get('createdAt')])
            if self.mapped('todo', legacy, Card):
                self.skipped['todos'] += 1; continue
            try:
                due = date.fromisoformat(t['due']) if isinstance(t.get('due'), str) and t['due'] else None
            except ValueError:
                due = None
            prio = t.get('prio')
            prio = min(2, max(0, int(prio))) if isinstance(prio, (int, float)) and not isinstance(prio, bool) else 0
            pid = project_ids.get(t.get('projectId')) if t.get('projectId') else None
            cid = None if pid else self.customer(t.get('customer'))
            done_at = (from_ms(t.get('doneAt')) or from_ms(t.get('createdAt')) or now()) if t.get('done') else None
            if board is None:
                board = ensure_default_board(self.db, self.u.id)
                all_cols = list(self.db.scalars(select(BoardColumn).where(BoardColumn.board_id == board.id).order_by(BoardColumn.position)))
                cols = [next((c for c in all_cols if not c.is_done), all_cols[0]), next((c for c in all_cols if c.is_done), all_cols[-1])]
            col = cols[-1] if done_at else cols[0]
            obj = Card(board_id=board.id, column_id=col.id, position=next_pos(col.id), title=text, description='', due=due, priority=prio,
                       project_id=pid, customer_id=cid, created_by=self.u.id, done_at=utc(done_at), created_at=utc(from_ms(t.get('createdAt')) or now()))
            self.db.add(obj); self.db.flush()
            self.remember('todo', legacy, obj.id)
            self.created['todos'] += 1

    def days(self, attendance):
        existing = set(self.db.scalars(select(WorkDay.date).where(WorkDay.user_id == self.u.id)))
        for k, rec in sorted(attendance.items()):
            try:
                day = date.fromisoformat(k)
            except (TypeError, ValueError):
                self.warn(f'Neplatný dátum dochádzky „{k}“ bol vynechaný.'); continue
            segs = jc.clean(rec['segs'])
            if any(s['in'] and not _hm_ok(s['in']) or s['out'] and not _hm_ok(s['out']) for s in segs):
                self.warn(f'Deň {day:%d.%m.%Y} má neplatný formát času a bol vynechaný.'); continue
            err = jc.validate(segs)
            if err:
                self.warn(f'Deň {day:%d.%m.%Y} bol vynechaný: {err}'); continue
            if not segs: continue
            if day in existing:
                self.skipped['days'] += 1; self.skipped['segments'] += len(segs); continue
            store(self.db, self.u.id, day, {'segs': segs, 'done': rec['done']})
            existing.add(day)
            self.created['days'] += 1; self.created['segments'] += len(segs)


def _hm_ok(v):
    if not isinstance(v, str) or len(v) != 5 or v[2] != ':': return False
    try:
        return 0 <= int(v[:2]) <= 23 and 0 <= int(v[3:]) <= 59
    except ValueError:
        return False


def run_import(db, u, payload, dry_run: bool) -> dict:
    """Celý import v jednej transakcii; pri dry_run sa na konci všetko vráti späť (presný náhľad)."""
    state = normalize(payload, now())
    imp = Importer(db, u)
    # zákazníci zo súboru (projekty, úlohy a to-do bez projektu) sa vyhodnotia vopred, aby súhrn sedel aj pri opakovanom importe
    for name in [p.get('customer') for p in state['projects']] + [t.get('customer') for t in state['tasks'] + state['todos'] if not t.get('projectId')]:
        imp.customer(name)
    project_ids = imp.projects(state['projects'])
    def ids(items, key, keep):   # iba hashovateľné identifikátory; množiny namiesto vnorených prechodov (rýchlosť)
        return {x.get(key) for x in items if isinstance(x.get(key), (str, int)) and keep(x)}
    is_sample = lambda a: text_of(a.get('text'), 5000) == SAMPLE_ACTIVITY
    sample_tasks = (ids(state['activities'], 'taskId', is_sample) - ids(state['activities'], 'taskId', lambda a: not is_sample(a))) \
        & ids(state['tasks'], 'id', lambda t: t.get('name') == 'Príklad: prevzatie serverov')
    sample_tasks.discard(''); sample_tasks.discard(0)
    task_ids = imp.tasks(state['tasks'], project_ids, sample_tasks)
    imp.activities(state['activities'], task_ids)
    imp.todos(state['todos'], project_ids)
    imp.days(state['attendance'])
    result = {'dry_run': dry_run, 'created': imp.created, 'skipped': imp.skipped, 'warnings': imp.warnings}
    if dry_run:
        db.rollback()
    else:
        audit(db, u, 'journal.import', {'created': imp.created, 'skipped': imp.skipped})
        db.commit()
    return result

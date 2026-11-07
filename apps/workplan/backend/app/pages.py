"""Viditeľnosť podstránok Workplanu podľa skupín z portálu a jednotlivcov.

Nastavenie (application_settings['page_access']): {'groups': {id skupiny: [stránky]}, 'users': {id používateľa: [stránky]}}.
Pravidlá: admin vidí všetko. Vlastné nastavenie človeka má prednosť pred skupinami. Inak vidí zjednotenie toho,
čo povoľujú jeho skupiny; skupina bez nastavenia (a človek bez skupiny) povoľuje všetko.
Skrytá stránka zmizne z menu a server odmietne jej požiadavky (pozri `check_request`).
"""
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import get_db
from .models import Setting, User
from .common import audit, commit
from .security import admin

router = APIRouter(prefix='/api', tags=['pages'])
PAGES = {'calendar': 'Dochádzka', 'journal': 'Denník', 'dashboard': 'Prehľad', 'requests': 'Žiadosti'}
KEY = 'page_access'


def config(db) -> dict:
    s = db.get(Setting, KEY)
    v = s.value if s else {}
    return {'groups': dict(v.get('groups') or {}), 'users': dict(v.get('users') or {})}


def allowed_pages(db, user) -> list[str]:
    if user.role == 'admin': return list(PAGES)
    cfg = config(db)
    own = cfg['users'].get(str(user.id))
    if own is not None: return [p for p in PAGES if p in own]
    gids = {str(g) for g in (getattr(user, 'group_ids', None) or set())}
    if not gids or any(g not in cfg['groups'] for g in gids): return list(PAGES)
    union = set().union(*(cfg['groups'][g] for g in gids))
    return [p for p in PAGES if p in union]


def needed(method: str, path: str) -> set[str] | None:
    """Ktorá stránka musí byť viditeľná pre túto požiadavku (stačí jedna z množiny). None = vždy povolené."""
    if path.startswith('/api/journal/'): return {'journal'}
    if path in ('/api/calendar', '/api/statistics') or path.startswith('/api/hours'): return {'calendar', 'dashboard'}
    if path.startswith('/api/attendance'): return {'calendar'}
    if path.startswith('/api/requests') and method != 'GET': return {'requests'}
    return None


def check_request(request: Request, db, user):
    need = needed(request.method, request.url.path)
    if need and not need & set(allowed_pages(db, user)):
        raise HTTPException(403, 'Táto časť Workplanu vám nie je sprístupnená. Obráťte sa na správcu.')


# ---------------------------------------------------------------- správa (admin)

class AccessIn(BaseModel):
    groups: dict[str, list[str] | None] = Field(default_factory=dict)
    users: dict[str, list[str] | None] = Field(default_factory=dict)


def clean_map(m: dict) -> dict:
    out = {}
    for k, pages in m.items():
        if not k.isdigit(): raise HTTPException(422, 'Neplatné ID skupiny alebo používateľa.')
        if pages is None: continue          # bez obmedzenia
        bad = set(pages) - set(PAGES)
        if bad: raise HTTPException(422, 'Neznáma podstránka: ' + ', '.join(sorted(bad)))
        if not pages: raise HTTPException(422, 'Každý musí vidieť aspoň jednu podstránku.')
        out[k] = [p for p in PAGES if p in pages]
    return out


def overview(db) -> dict:
    from .directory import members
    people = members(db) or {}
    groups = {}
    for p in people.values():
        for g in p['groups']:
            groups.setdefault(g['id'], {'id': g['id'], 'name': g['name'], 'members': 0})['members'] += 1
    by_email = {u.email: u for u in db.scalars(select(User).where(User.active == True))}  # noqa: E712
    users = []
    for email, p in sorted(people.items(), key=lambda x: x[1]['name'].casefold()):
        u = by_email.get(email)
        if not u: continue
        u.group_ids = {g['id'] for g in p['groups']}
        users.append({'id': u.id, 'name': u.name, 'admin': u.role == 'admin', 'group_ids': sorted(u.group_ids),
                      'groups': [g['name'] for g in p['groups']], 'effective': allowed_pages(db, u)})
    return {'pages': [{'key': k, 'label': v} for k, v in PAGES.items()],
            'groups': sorted(groups.values(), key=lambda g: g['name'].casefold()), 'users': users, 'config': config(db)}


@router.get('/page-access')
def get_access(u=Depends(admin), db: Session = Depends(get_db)):
    return overview(db)


@router.put('/page-access')
def put_access(p: AccessIn, u=Depends(admin), db: Session = Depends(get_db)):
    value = {'groups': clean_map(p.groups), 'users': clean_map(p.users)}
    s = db.get(Setting, KEY)
    if s: s.value = value
    else: db.add(Setting(key=KEY, value=value))
    audit(db, u, 'pages.update', value)
    commit(db)
    return overview(db)

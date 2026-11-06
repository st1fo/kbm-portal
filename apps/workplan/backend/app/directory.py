"""Ľudia z portálu KBM: zakladanie lokálnych účtov a viditeľnosť podľa skupín.

Portál je zdroj pravdy pre identitu, rolu, prístup k aplikáciám a skupiny. Workplan si podľa e-mailu drží
lokálny riadok (úväzok, aktivita, plány). Zamestnanec vidí seba a ľudí, s ktorými má spoločnú skupinu;
administrátor vidí všetkých s prístupom k Workplanu.
"""
import secrets
from sqlalchemy import select
from .models import User
from .portal_sso import portal_directory
from .security import hasher
from .common import get_settings

APP = 'workplan'


def ensure_user(db, identity):
    """Lokálny riadok pre účet z portálu; meno a rola sa zapíšu iba pri skutočnej zmene."""
    email = identity['email'].lower()
    role = 'admin' if identity['admin'] else 'employee'
    user = db.scalar(select(User).where(User.email == email))
    if not user:
        user = User(name=identity['name'], email=email, role=role, password_hash=hasher.hash(secrets.token_urlsafe(32)),
                    daily_hours=get_settings(db).get('daily_hours', 8))
        db.add(user)
        db.commit()
    elif user.name != identity['name'] or user.role != role:
        user.name, user.role = identity['name'], role
        db.commit()
    return user


def members(db):
    """Účty portálu s prístupom k Workplanu {email: záznam}; chýbajúce lokálne riadky sa doplnia. None = portál nedostupný."""
    directory = portal_directory()
    if directory is None:
        return None
    out = {p['email'].lower(): p for p in directory if p['active'] and APP in p['apps']}
    for p in out.values():
        ensure_user(db, p)
    return out


def visible_user_ids(db, user):
    """ID používateľov, ktorých smie `user` vidieť v kalendári a prehľadoch. None = všetci (admin bez adresára)."""
    people = members(db)
    if people is None:
        return None if user.role == 'admin' else {user.id}
    if user.role == 'admin':
        allowed = set(people)
    else:
        mine = getattr(user, 'group_ids', None)
        if mine is None:
            mine = {g['id'] for g in people.get(user.email, {}).get('groups', [])}
        allowed = {e for e, p in people.items() if mine & {g['id'] for g in p['groups']}} | {user.email}
    return set(db.scalars(select(User.id).where(User.email.in_(allowed))))


def group_names(db):
    """E-mail → názvy skupín (pre zoznam zamestnancov a filter v kalendári)."""
    people = members(db) or {}
    return {e: [g['name'] for g in p['groups']] for e, p in people.items()}


def group_member_ids(db, name):
    """ID lokálnych používateľov v skupine `name` (podľa názvu, bez ohľadu na veľkosť písmen). None = portál nedostupný."""
    people = members(db)
    if people is None:
        return None
    key = name.strip().casefold()
    emails = {e for e, p in people.items() if any(g['name'].casefold() == key for g in p['groups'])}
    return set(db.scalars(select(User.id).where(User.email.in_(emails)))) if emails else set()


def restrict_to_group(db, ids, group):
    """Zúži viditeľných používateľov (None = všetci) na jednu skupinu; bez skupiny vráti ids bez zmeny."""
    if not group:
        return ids
    members_ = group_member_ids(db, group)
    if members_ is None:
        return ids
    return members_ if ids is None else ids & members_

import hashlib
import hmac
import os
import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

from .db import Login, Session, User, now

COOKIE = "logbook_session"

def db_session():
    with Session() as db:
        yield db

def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return f"{salt}:{digest}"

def check_password(password, stored):
    return hmac.compare_digest(hash_password(password, stored.split(":")[0]), stored)

DUMMY = hash_password("temporary-" + secrets.token_hex(16))

def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()

def login(db, response, user):
    token = secrets.token_urlsafe(32)
    expires = now() + timedelta(hours=12)
    db.add(Login(digest=digest(token), expires=expires, user_id=user.id))
    db.commit()
    response.set_cookie(COOKIE, token, httponly=True, secure=os.getenv("COOKIE_SECURE", "1") == "1",
                        samesite="strict", max_age=43200, path="/api")

def current_user(request: Request, db=Depends(db_session)):
    from .portal_sso import portal_identity, require_app
    identity=portal_identity(request)
    require_app(identity,'vehicle-logbook')
    user=db.scalar(select(User).where(User.username==identity['slug']))
    if not user:
        user=User(username=identity['slug'],password=hash_password(secrets.token_urlsafe(32)),admin=bool(identity['admin']),active=True)
        db.add(user); db.commit(); db.refresh(user)
    if not user.active: raise HTTPException(403, "Účet je deaktivovaný v Knihe jázd.")
    if user.admin != bool(identity['admin']):
        user.admin=bool(identity['admin']); db.commit()
    user.display_name=identity.get('name') or user.username  # shown as "Vypracoval" in exports
    return user

def administrator(user=Depends(current_user)):
    if not user.admin:
        raise HTTPException(403, "Túto akciu môže vykonať iba administrátor.")
    return user


def sync_directory(db):
    """Účty portálu s prístupom ku Knihe jázd {username: záznam}; chýbajúce lokálne riadky sa doplnia, aby im admin
    mohol priradiť vozidlá ešte pred prvým prihlásením. None = portál nedostupný."""
    from .portal_sso import portal_directory
    directory = portal_directory()
    if directory is None:
        return None
    people = {p["slug"]: p for p in directory if p["active"] and "vehicle-logbook" in p["apps"]}
    existing = {u.username: u for u in db.scalars(select(User))}
    for slug, p in people.items():
        if slug not in existing:
            db.add(User(username=slug, password=hash_password(secrets.token_urlsafe(32)), admin=bool(p["admin"]), active=True))
        elif existing[slug].admin != bool(p["admin"]):
            existing[slug].admin = bool(p["admin"])
    db.commit()
    return people

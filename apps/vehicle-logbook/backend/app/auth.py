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
    from .portal_sso import portal_identity
    identity=portal_identity(request)
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

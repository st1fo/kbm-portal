import os
from datetime import datetime, timedelta, timezone
import jwt
from argon2 import PasswordHasher
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session
from .db import get_db
from .models import User

hasher = PasswordHasher()
SECRET = os.getenv('JWT_SECRET', '')
if len(SECRET) < 32:
    raise RuntimeError('Set JWT_SECRET to a random secret of at least 32 characters.')
bearer = HTTPBearer()
def token(user):
    return jwt.encode({'sub': str(user.id), 'iat': datetime.now(timezone.utc), 'exp': datetime.now(timezone.utc)+timedelta(hours=8)}, SECRET, algorithm='HS256')
def current_user(request: Request, db: Session = Depends(get_db)):
    from .portal_sso import portal_identity
    from sqlalchemy import select
    identity=portal_identity(request)
    user=db.scalar(select(User).where(User.email==identity['email']))
    if not user:
        raise HTTPException(403, 'Účet nie je priradený k Workplanu.')
    if not user.active: raise HTTPException(403, 'Účet je deaktivovaný vo Workplane.')
    user.name=identity['name']; user.role='admin' if identity['admin'] else 'employee'
    db.commit()
    return user
def admin(user=Depends(current_user)):
    if user.role != 'admin': raise HTTPException(403, 'Vyžaduje sa administrátor.')
    return user

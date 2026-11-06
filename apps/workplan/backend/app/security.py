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
    from .portal_sso import portal_identity, require_app
    from .directory import ensure_user
    identity=portal_identity(request)
    require_app(identity,'workplan')
    user=ensure_user(db,identity)  # nový účet z portálu sa založí pri prvej požiadavke
    if not user.active: raise HTTPException(403, 'Účet je deaktivovaný vo Workplane.')
    user.group_ids={g['id'] for g in identity.get('groups',[])}
    return user
def admin(user=Depends(current_user)):
    if user.role != 'admin': raise HTTPException(403, 'Vyžaduje sa administrátor.')
    return user

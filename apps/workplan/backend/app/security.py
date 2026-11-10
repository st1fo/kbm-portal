from argon2 import PasswordHasher
from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session
from .db import get_db

# Prihlasuje spoločný portál; lokálne heslo je iba náhodná nepoužiteľná výplň povinného stĺpca.
hasher = PasswordHasher()
def current_user(request: Request, db: Session = Depends(get_db)):
    from .portal_sso import portal_identity, require_app
    from .directory import ensure_user
    identity=portal_identity(request)
    require_app(identity,'workplan')
    user=ensure_user(db,identity)  # nový účet z portálu sa založí pri prvej požiadavke
    if not user.active: raise HTTPException(403, 'Účet je deaktivovaný vo Workplane.')
    user.group_ids={g['id'] for g in identity.get('groups',[])}
    from .pages import check_request
    check_request(request,db,user)  # skryté podstránky (Administrácia → Viditeľnosť podstránok)
    return user
def admin(user=Depends(current_user)):
    if user.role != 'admin': raise HTTPException(403, 'Vyžaduje sa administrátor.')
    return user

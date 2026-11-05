import os, secrets
from sqlalchemy import select
from .db import SessionLocal
from .models import User
from .security import hasher

def main():
    with SessionLocal() as db:
        for name,slug in [('Maroš Sokolák','maros'),('Zdenko Sokolovský','zdenko'),('Florián Žifčák','florian'),('Martin Zelenák','martin')]:
            if not db.scalar(select(User).where(User.email==slug+'@demo.kbm.local')):
                db.add(User(name=name,email=slug+'@demo.kbm.local',password_hash=hasher.hash(secrets.token_urlsafe(32)),role='admin' if slug=='maros' else 'employee'))
        db.commit()
if __name__=='__main__': main()

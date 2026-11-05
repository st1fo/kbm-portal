import secrets
from sqlalchemy import select
from .auth import hash_password
from .db import Session, User

def main():
    with Session() as db:
        for slug in ['maros','zdenko','florian','martin']:
            if not db.scalar(select(User).where(User.username==slug)):
                db.add(User(username=slug,password=hash_password(secrets.token_urlsafe(32)),admin=slug=='maros',active=True))
        db.commit()
if __name__=='__main__': main()

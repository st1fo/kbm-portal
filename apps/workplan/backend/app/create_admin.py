"""Interactive first production administrator; password stays off the command line."""
from getpass import getpass
from sqlalchemy import select
from .db import SessionLocal
from .models import User
from .security import hasher
from .schemas import Employee

def main():
    name=input('Meno: ').strip(); email=input('E-mail: ').strip().lower()
    password=getpass('Heslo (aspoň 10 znakov): ')
    if password!=getpass('Zopakujte heslo: '): raise SystemExit('Heslá sa nezhodujú.')
    p=Employee(name=name,email=email,password=password,role='admin')
    with SessionLocal() as db:
        if db.scalar(select(User).where(User.email==p.email)): raise SystemExit('Účet už existuje.')
        db.add(User(name=p.name,email=p.email,password_hash=hasher.hash(password),role='admin'))
        db.commit()
    print('Administrátor vytvorený.')
if __name__=='__main__':main()

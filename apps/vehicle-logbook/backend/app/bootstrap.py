import getpass

from sqlalchemy import select, text
from .auth import hash_password
from .db import Session, User

def main():
    username = input("Prihlasovacie meno správcu: ").strip().lower()
    if not 1 <= len(username) <= 80:
        raise SystemExit("Meno musí mať 1 až 80 znakov.")
    password = getpass.getpass("Heslo (aspoň 12 znakov): ")
    if not 12 <= len(password) <= 256 or password != getpass.getpass("Zopakujte heslo: "):
        raise SystemExit("Heslá sa nezhodujú alebo majú nesprávnu dĺžku.")
    with Session() as db:
        if db.bind.dialect.name == "postgresql":
            db.execute(text("SELECT pg_advisory_xact_lock(6250242)"))
        if db.scalar(select(User.id).limit(1)):
            raise SystemExit("Prvý účet už existuje. Použite správu používateľov v aplikácii.")
        db.add(User(username=username,password=hash_password(password),admin=True))
        db.commit()
    print("Správca bol vytvorený.")

if __name__ == "__main__":
    main()

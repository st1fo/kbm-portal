import getpass, sys
from main import connect, hashed

def main():
    if len(sys.argv)!=2: raise SystemExit('Použitie: python reset_password.py maros')
    slug=sys.argv[1].strip().lower()
    password=getpass.getpass('Nové heslo (aspoň 12 znakov): ')
    if not 12<=len(password)<=256 or password!=getpass.getpass('Zopakujte nové heslo: '):
        raise SystemExit('Heslá sa nezhodujú alebo majú nesprávnu dĺžku.')
    with connect() as db:
        if not db.execute('SELECT 1 FROM users WHERE slug=?',(slug,)).fetchone(): raise SystemExit('Účet neexistuje.')
        db.execute('UPDATE users SET password=? WHERE slug=?',(hashed(password),slug))
        db.execute('DELETE FROM sessions WHERE slug=?',(slug,))
    print('Heslo zmenené; existujúce relácie boli zrušené.')
if __name__=='__main__':main()

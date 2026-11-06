import os, sqlite3, secrets, hashlib, hmac, time, json, re, smtplib, ssl
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from email.message import EmailMessage
from typing import Literal
from urllib.parse import urlsplit
from urllib.request import Request as URLRequest, urlopen
from urllib.error import HTTPError, URLError
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

DB=os.getenv("PORTAL_DB", "/data/portal.sqlite")
COOKIE="kbm_portal_session"
PEOPLE=[("maros", "Maroš Sokolák", True),("zdenko", "Zdenko Sokolovský", False),("florian", "Florián Žifčák", False),("martin", "Martin Zelenák", False)]
APPS={"workplan":"Workplan","vehicle-logbook":"Kniha jázd"}
ALL_APPS=json.dumps(list(APPS))
# Úvodné skupiny (admin ich môže premenovať, mazať a pridávať ďalšie) a zaradenie pôvodných štyroch účtov.
DEFAULT_GROUPS=["Vedenie","Vývojári","Predaj","Technici","Administratíva","Brigádnici"]
DEFAULT_MEMBERS={"maros":"Vedenie","zdenko":"Vývojári","florian":"Vývojári","martin":"Vývojári"}
INVITE_HOURS=72
ATTEMPTS=defaultdict(deque)
def connect():
    db=sqlite3.connect(DB, timeout=15); db.row_factory=sqlite3.Row; db.execute("PRAGMA foreign_keys=ON"); return db
def hashed(password, salt=None):
    salt=salt or secrets.token_hex(16)
    return salt+":"+hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
def verify(password, stored): return hmac.compare_digest(hashed(password,stored.split(":")[0]),stored)
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
DUMMY=hashed(secrets.token_urlsafe(32))

def migrate(db):
    """Idempotentná schéma; pôvodné tabuľky users/sessions sa rozšíria bez straty údajov."""
    db.executescript("CREATE TABLE IF NOT EXISTS users (slug TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, admin INTEGER NOT NULL, password TEXT NOT NULL); CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, slug TEXT NOT NULL, expires REAL NOT NULL);")
    cols={r[1] for r in db.execute("PRAGMA table_info(users)")}
    if "active" not in cols: db.execute("ALTER TABLE users ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
    if "apps" not in cols: db.execute(f"ALTER TABLE users ADD COLUMN apps TEXT NOT NULL DEFAULT '{ALL_APPS}'")
    if "created_at" not in cols: db.execute("ALTER TABLE users ADD COLUMN created_at REAL")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS groups (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE);
    CREATE TABLE IF NOT EXISTS user_groups (slug TEXT NOT NULL REFERENCES users(slug) ON DELETE CASCADE, group_id INTEGER NOT NULL REFERENCES groups(id) ON DELETE CASCADE, PRIMARY KEY (slug, group_id));
    CREATE TABLE IF NOT EXISTS invites (digest TEXT PRIMARY KEY, slug TEXT NOT NULL REFERENCES users(slug) ON DELETE CASCADE, kind TEXT NOT NULL, expires REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, ts REAL NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, detail TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    if not db.execute("SELECT 1 FROM meta WHERE key='groups_seeded'").fetchone():
        for name in DEFAULT_GROUPS: db.execute("INSERT OR IGNORE INTO groups (name) VALUES (?)",(name,))
        for slug,group in DEFAULT_MEMBERS.items():
            if db.execute("SELECT 1 FROM users WHERE slug=?",(slug,)).fetchone():
                db.execute("INSERT OR IGNORE INTO user_groups SELECT ?, id FROM groups WHERE name=?",(slug,group))
        db.execute("INSERT INTO meta VALUES ('groups_seeded','1')")

@asynccontextmanager
async def lifespan(app):
    if len(os.environ.get("SSO_INTERNAL_KEY", ""))<32: raise RuntimeError("SSO_INTERNAL_KEY is required")
    with connect() as db:
        db.executescript("CREATE TABLE IF NOT EXISTS users (slug TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, admin INTEGER NOT NULL, password TEXT NOT NULL); CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, slug TEXT NOT NULL, expires REAL NOT NULL);")
        if not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            password=os.environ.get("DEMO_PASSWORD", "")
            if len(password)<12: raise RuntimeError("DEMO_PASSWORD needs at least 12 characters")
            for slug,name,admin in PEOPLE:
                db.execute("INSERT INTO users (slug,name,email,admin,password) VALUES (?,?,?,?,?)",(slug,name,slug+"@demo.kbm.local",admin,hashed(password)))
        migrate(db)
    yield
app=FastAPI(title="KBM Portal – testovací prototyp",lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
@app.middleware("http")
async def protect(request, call_next):
    if request.method in {"POST","PUT","PATCH","DELETE"}:
        origin=request.headers.get("origin")
        if request.headers.get("sec-fetch-site") in {"cross-site","same-site"} or (origin and urlsplit(origin).netloc != request.headers.get("host")):
            return JSONResponse({"detail":"Nepovolený pôvod požiadavky."},status_code=403)
    response=await call_next(request)
    response.headers["Cache-Control"]="no-store"
    return response

def groups_of(db, slugs=None):
    rows=db.execute("SELECT ug.slug, g.id, g.name FROM user_groups ug JOIN groups g ON g.id=ug.group_id ORDER BY g.name").fetchall()
    out=defaultdict(list)
    for r in rows:
        if slugs is None or r["slug"] in slugs: out[r["slug"]].append({"id":r["id"],"name":r["name"]})
    return out
def public_user(row, groups):
    return {"slug":row["slug"],"name":row["name"],"email":row["email"],"admin":bool(row["admin"]),"active":bool(row["active"]),
            "apps":[a for a in json.loads(row["apps"] or "[]") if a in APPS],"groups":groups.get(row["slug"],[]),
            "created_at":row["created_at"]}
def identity(request):
    with connect() as db:
        user=db.execute("SELECT u.* FROM users u JOIN sessions s ON s.slug=u.slug WHERE s.digest=? AND s.expires>? AND u.active=1",(digest(request.cookies.get(COOKIE,"")),time.time())).fetchone()
        if not user: raise HTTPException(401,"Prihláste sa do portálu.")
        data=public_user(user, groups_of(db,{user["slug"]}))
    data.pop("created_at")
    return data
def admin_only(request):
    user=identity(request)
    if not user["admin"]: raise HTTPException(403,"Túto akciu môže vykonať iba administrátor portálu.")
    return user
def audit(db, actor, action, detail):
    db.execute("INSERT INTO audit (ts,actor,action,detail) VALUES (?,?,?,?)",(time.time(),actor,action,json.dumps(detail,ensure_ascii=False)[:2000]))

class Credentials(BaseModel):
    username: str = Field(min_length=1,max_length=254)
    password: str = Field(min_length=1,max_length=256)
class PasswordChange(BaseModel):
    current: str = Field(min_length=1,max_length=256)
    password: str = Field(min_length=12,max_length=256)
@app.get("/auth/health")
def health():
    with connect() as db: db.execute("SELECT 1")
    return {"status":"ok"}
def limited(request):
    # Nginx overwrites this header; only the gateway is exposed.
    key=request.headers.get("x-real-ip",request.client.host)
    bucket=ATTEMPTS[key]; now=time.time()
    while bucket and bucket[0]<now-60: bucket.popleft()
    if len(bucket)>=10: raise HTTPException(429,"Príliš veľa pokusov. Skúste o minútu.")
    bucket.append(now)
@app.post("/auth/login")
def login(data:Credentials, request:Request, response:Response):
    limited(request); now=time.time()
    with connect() as db:
        value=data.username.strip().lower()
        user=db.execute("SELECT * FROM users WHERE slug=? OR email=?",(value,value)).fetchone()
        valid=verify(data.password,user["password"] if user else DUMMY)
        if not user or not valid or not user["active"]: raise HTTPException(401,"Nesprávne prihlasovacie meno alebo heslo.")
        token=secrets.token_urlsafe(32)
        db.execute("DELETE FROM sessions WHERE expires<=? OR digest=?",(now,digest(request.cookies.get(COOKIE,""))))
        db.execute("INSERT INTO sessions VALUES (?,?,?)",(digest(token),user["slug"],now+43200))
    response.set_cookie(COOKIE,token,httponly=True,secure=os.getenv("COOKIE_SECURE","0")=="1",samesite="lax",max_age=43200,path="/")
    return {"ok":True}
@app.get("/auth/me")
def me(request:Request): return identity(request)
@app.get("/auth/app/{name}")
def app_access(name:str, request:Request):
    """Pre nginx auth_request: 401 bez prihlásenia, 403 bez prístupu k aplikácii."""
    user=identity(request)
    if name not in user["apps"]: raise HTTPException(403,"Nemáte prístup k tejto aplikácii.")
    return {"ok":True}
@app.post("/auth/logout")
def logout(request:Request,response:Response):
    with connect() as db: db.execute("DELETE FROM sessions WHERE digest=?",(digest(request.cookies.get(COOKIE,"")),))
    response.delete_cookie(COOKIE,path="/"); return {"ok":True}
@app.post("/auth/password")
def password(data:PasswordChange,request:Request,response:Response):
    user=identity(request)
    with connect() as db:
        row=db.execute("SELECT password FROM users WHERE slug=?",(user["slug"],)).fetchone()
        if not verify(data.current,row["password"]): raise HTTPException(400,"Aktuálne heslo nie je správne.")
        db.execute("UPDATE users SET password=? WHERE slug=?",(hashed(data.password),user["slug"]))
        db.execute("DELETE FROM sessions WHERE slug=?",(user["slug"],))
    response.delete_cookie(COOKIE,path="/"); return {"ok":True}

# ---------------------------------------------------------------- pozvánky a e-mail

def base_url(request):
    """Adresa portálu do odkazu: PUBLIC_URL, inak pôvod požiadavky admina (funguje aj za tunelom)."""
    if os.getenv("PUBLIC_URL"): return os.environ["PUBLIC_URL"].rstrip("/")
    origin=request.headers.get("origin")
    if origin: return origin.rstrip("/")
    return f"{request.url.scheme}://{request.headers.get('host')}"
def new_invite(db, slug, kind):
    token=secrets.token_urlsafe(32)
    db.execute("DELETE FROM invites WHERE slug=?",(slug,))
    db.execute("INSERT INTO invites VALUES (?,?,?,?)",(digest(token),slug,kind,time.time()+INVITE_HOURS*3600))
    return token
def smtp_ready(): return bool(os.getenv("SMTP_HOST") and os.getenv("SMTP_FROM"))
def send_mail(to, subject, body):
    """Odošle e-mail cez SMTP z .env; vráti '' alebo text chyby (odkaz potom admin pošle ručne)."""
    if not smtp_ready(): return "Odosielanie e-mailov nie je nastavené (SMTP v .env)."
    msg=EmailMessage(); msg["From"]=os.environ["SMTP_FROM"]; msg["To"]=to; msg["Subject"]=subject; msg.set_content(body)
    host,port=os.environ["SMTP_HOST"],int(os.getenv("SMTP_PORT","587"))
    try:
        context=ssl.create_default_context()
        with (smtplib.SMTP_SSL(host,port,timeout=15,context=context) if port==465 else smtplib.SMTP(host,port,timeout=15)) as s:
            if port!=465: s.starttls(context=context)
            if os.getenv("SMTP_USER"): s.login(os.environ["SMTP_USER"],os.getenv("SMTP_PASSWORD",""))
            s.send_message(msg)
        return ""
    except (OSError, smtplib.SMTPException) as e:
        return f"E-mail sa nepodarilo odoslať ({type(e).__name__})."
def invite_mail(user, url, kind):
    apps=", ".join(APPS[a] for a in json.loads(user["apps"] or "[]") if a in APPS) or "zatiaľ žiadne"
    if kind=="invite":
        subject="Pozvánka do portálu KBM"
        intro=f"Dobrý deň, {user['name']},\n\nbol vám vytvorený účet v portáli KBM (aplikácie: {apps})."
    else:
        subject="Nastavenie nového hesla – portál KBM"
        intro=f"Dobrý deň, {user['name']},\n\nadministrátor vám umožnil nastaviť si nové heslo do portálu KBM."
    return subject, f"{intro}\n\nPrihlasovacie meno: {user['slug']}\nHeslo si nastavíte tu (odkaz platí {INVITE_HOURS} hodín):\n{url}\n\nAk ste o to nežiadali, správu ignorujte.\n\nKBM.DIGITAL"
def prepare_invite(db, request, slug, kind):
    """Vytvorí odkaz v transakcii; e-mail sa posiela až po commite (send_invite), aby SMTP nedržal zámok DB."""
    user=dict(db.execute("SELECT * FROM users WHERE slug=?",(slug,)).fetchone())
    return user, f"{base_url(request)}/?invite={new_invite(db, slug, kind)}", kind
def send_invite(prepared, send):
    user,url,kind=prepared
    mail_error=send_mail(user["email"], *invite_mail(user, url, kind)) if send else ""
    return {"invite_url":url,"emailed":send and not mail_error,"email_error":mail_error,"expires_hours":INVITE_HOURS}
def invite_row(db, token):
    return db.execute("SELECT i.kind, u.slug, u.name, u.active FROM invites i JOIN users u ON u.slug=i.slug WHERE i.digest=? AND i.expires>?",(digest(token),time.time())).fetchone()
class InviteAccept(BaseModel):
    token: str = Field(min_length=20,max_length=100)
    password: str = Field(min_length=12,max_length=256)
@app.get("/auth/invite")
def invite_info(token:str):
    with connect() as db: row=invite_row(db, token)
    if not row or not row["active"]: raise HTTPException(404,"Odkaz je neplatný alebo vypršal. Požiadajte administrátora o nový.")
    return {"name":row["name"],"username":row["slug"],"kind":row["kind"]}
@app.post("/auth/invite")
def invite_accept(data:InviteAccept, request:Request):
    limited(request)
    with connect() as db:
        row=invite_row(db, data.token)
        if not row or not row["active"]: raise HTTPException(404,"Odkaz je neplatný alebo vypršal. Požiadajte administrátora o nový.")
        db.execute("UPDATE users SET password=? WHERE slug=?",(hashed(data.password),row["slug"]))
        db.execute("DELETE FROM invites WHERE slug=?",(row["slug"],))
        db.execute("DELETE FROM sessions WHERE slug=?",(row["slug"],))
        audit(db, row["slug"], "password.set", {"via":row["kind"]})
    return {"ok":True,"username":row["slug"]}

# ---------------------------------------------------------------- správa používateľov a skupín (admin)

SLUG_RE=r"^[a-z0-9][a-z0-9._-]{1,39}$"
EMAIL_RE=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
AppName=Literal["workplan","vehicle-logbook"]
def clean_name(v):
    v=" ".join(v.split())
    if len(v)<2: raise ValueError("Zadajte meno a priezvisko.")
    return v
class UserIn(BaseModel):
    name: str = Field(min_length=2,max_length=150)
    username: str = Field(pattern=SLUG_RE)
    email: str = Field(max_length=254,pattern=EMAIL_RE)
    admin: bool = False
    apps: list[AppName] = Field(default_factory=lambda: list(APPS))
    groups: list[int] = Field(default_factory=list,max_length=50)
    send_email: bool = True
    @field_validator("name")
    @classmethod
    def _name(cls,v): return clean_name(v)
class UserPatch(BaseModel):
    name: str | None = Field(default=None,min_length=2,max_length=150)
    admin: bool | None = None
    active: bool | None = None
    apps: list[AppName] | None = None
    groups: list[int] | None = Field(default=None,max_length=50)
    @field_validator("name")
    @classmethod
    def _name(cls,v): return clean_name(v) if v is not None else v
class GroupIn(BaseModel):
    name: str = Field(min_length=2,max_length=60)
    @field_validator("name")
    @classmethod
    def _name(cls,v): return clean_name(v)
class SendIn(BaseModel):
    send_email: bool = True

def set_groups(db, slug, ids):
    ids=sorted(set(ids))
    if ids and len(db.execute(f"SELECT id FROM groups WHERE id IN ({','.join('?'*len(ids))})",ids).fetchall())!=len(ids):
        raise HTTPException(422,"Zvolená skupina neexistuje.")
    db.execute("DELETE FROM user_groups WHERE slug=?",(slug,))
    db.executemany("INSERT INTO user_groups VALUES (?,?)",[(slug,i) for i in ids])
def user_out(db, slug):
    row=db.execute("SELECT * FROM users WHERE slug=?",(slug,)).fetchone()
    return public_user(row, groups_of(db,{slug}))
@app.get("/auth/admin/users")
def admin_users(request:Request):
    admin_only(request)
    with connect() as db:
        groups=groups_of(db)
        pending={r["slug"]:r["kind"] for r in db.execute("SELECT slug, kind FROM invites WHERE expires>?",(time.time(),))}
        return [{**public_user(r,groups),"invite_pending":pending.get(r["slug"])} for r in db.execute("SELECT * FROM users ORDER BY name COLLATE NOCASE")]
@app.post("/auth/admin/users")
def admin_create(data:UserIn, request:Request):
    actor=admin_only(request)
    slug,email=data.username.lower(),data.email.strip().lower()
    with connect() as db:
        if db.execute("SELECT 1 FROM users WHERE slug=?",(slug,)).fetchone(): raise HTTPException(409,"Toto prihlasovacie meno už existuje.")
        if db.execute("SELECT 1 FROM users WHERE email=? OR slug=?",(email,email)).fetchone(): raise HTTPException(409,"Tento e-mail už používa iný účet.")
        db.execute("INSERT INTO users (slug,name,email,admin,password,active,apps,created_at) VALUES (?,?,?,?,?,1,?,?)",
                   (slug,data.name,email,int(data.admin),hashed(secrets.token_urlsafe(32)),json.dumps(sorted(set(data.apps))),time.time()))
        set_groups(db, slug, data.groups)
        audit(db, actor["slug"], "user.create", {"slug":slug,"admin":data.admin,"apps":data.apps,"groups":data.groups})
        prepared=prepare_invite(db, request, slug, "invite")
        out=user_out(db, slug)
    return {"user":out, **send_invite(prepared, data.send_email)}
@app.patch("/auth/admin/users/{slug}")
def admin_update(slug:str, data:UserPatch, request:Request):
    actor=admin_only(request)
    with connect() as db:
        row=db.execute("SELECT * FROM users WHERE slug=?",(slug,)).fetchone()
        if not row: raise HTTPException(404,"Používateľ neexistuje.")
        if slug==actor["slug"] and (data.admin is False or data.active is False):
            raise HTTPException(422,"Vlastný účet nemôžete deaktivovať ani mu odobrať správcu.")
        if data.name is not None: db.execute("UPDATE users SET name=? WHERE slug=?",(data.name,slug))
        if data.admin is not None: db.execute("UPDATE users SET admin=? WHERE slug=?",(int(data.admin),slug))
        if data.apps is not None: db.execute("UPDATE users SET apps=? WHERE slug=?",(json.dumps(sorted(set(data.apps))),slug))
        if data.groups is not None: set_groups(db, slug, data.groups)
        if data.active is not None:
            db.execute("UPDATE users SET active=? WHERE slug=?",(int(data.active),slug))
            if not data.active: db.execute("DELETE FROM sessions WHERE slug=?",(slug,)); db.execute("DELETE FROM invites WHERE slug=?",(slug,))
        audit(db, actor["slug"], "user.update", {"slug":slug, **data.model_dump(exclude_none=True)})
        return user_out(db, slug)
@app.post("/auth/admin/users/{slug}/invite")
def admin_reinvite(slug:str, data:SendIn, request:Request):
    """Nový odkaz na nastavenie hesla (pri zabudnutom hesle alebo ak pozvánka vypršala)."""
    actor=admin_only(request)
    with connect() as db:
        row=db.execute("SELECT active, password FROM users WHERE slug=?",(slug,)).fetchone()
        if not row: raise HTTPException(404,"Používateľ neexistuje.")
        if not row["active"]: raise HTTPException(409,"Účet je deaktivovaný. Najprv ho aktivujte.")
        audit(db, actor["slug"], "user.invite", {"slug":slug})
        prepared=prepare_invite(db, request, slug, "reset")
    return send_invite(prepared, data.send_email)
class DeleteIn(BaseModel):
    confirm: str = Field(min_length=1,max_length=60)
# Interné adresy aplikácií pre trvalé zmazanie údajov (v Compose cez Docker sieť, mimo nginx).
APP_INTERNAL={"workplan":"WORKPLAN_INTERNAL_URL","vehicle-logbook":"LOGBOOK_INTERNAL_URL"}
def purge_in_apps(user, actor):
    """Najprv zmaže údaje v oboch aplikáciách; ak niektorá neodpovie, účet v portáli ostane (opakovanie je bezpečné)."""
    results={}
    for app_name,env in APP_INTERNAL.items():
        base=os.getenv(env)
        if not base: raise HTTPException(503,f"Mazanie v aplikácii {APPS[app_name]} nie je nastavené ({env}).")
        payload={"email":user["email"],"actor_email":actor["email"]} if app_name=="workplan" else {"username":user["slug"]}
        req=URLRequest(base.rstrip("/")+"/api/internal/purge-user",data=json.dumps(payload).encode(),method="POST",
                       headers={"Content-Type":"application/json","X-SSO-Key":os.environ["SSO_INTERNAL_KEY"]})
        try:
            with urlopen(req,timeout=30) as response: results[app_name]=json.load(response)
        except (HTTPError,URLError,TimeoutError,OSError,ValueError):
            raise HTTPException(503,f"Aplikácia {APPS[app_name]} neodpovedá, účet sa nezmazal. Skúste to znova o chvíľu.")
    return results
@app.delete("/auth/admin/users/{slug}")
def admin_delete(slug:str, data:DeleteIn, request:Request):
    """Trvalé zmazanie používateľa a všetkých jeho údajov v portáli aj v aplikáciách. Nedá sa vrátiť."""
    actor=admin_only(request)
    if slug==actor["slug"]: raise HTTPException(422,"Vlastný účet nemôžete zmazať.")
    if data.confirm.strip().lower()!=slug: raise HTTPException(422,"Na potvrdenie napíšte presne prihlasovacie meno používateľa.")
    with connect() as db:
        row=db.execute("SELECT * FROM users WHERE slug=?",(slug,)).fetchone()
        if not row: raise HTTPException(404,"Používateľ neexistuje.")
        user=dict(row)
        db.execute("DELETE FROM sessions WHERE slug=?",(slug,))   # okamžité odhlásenie ešte pred mazaním v aplikáciách
    results=purge_in_apps(user, actor)
    with connect() as db:
        db.execute("DELETE FROM sessions WHERE slug=?",(slug,))
        db.execute("DELETE FROM users WHERE slug=?",(slug,))     # skupiny a pozvánky cez ON DELETE CASCADE
        audit(db, actor["slug"], "user.delete", {"slug":slug,"name":user["name"],"email":user["email"],**{k:v.get("counts",{}) for k,v in results.items()}})
    return {"ok":True,"results":results}
@app.get("/auth/admin/groups")
def admin_groups(request:Request):
    admin_only(request)
    with connect() as db:
        return [dict(r) for r in db.execute("SELECT g.id, g.name, COUNT(ug.slug) AS members FROM groups g LEFT JOIN user_groups ug ON ug.group_id=g.id GROUP BY g.id ORDER BY g.name COLLATE NOCASE")]
@app.post("/auth/admin/groups")
def admin_group_create(data:GroupIn, request:Request):
    actor=admin_only(request)
    with connect() as db:
        try: gid=db.execute("INSERT INTO groups (name) VALUES (?)",(data.name,)).lastrowid
        except sqlite3.IntegrityError: raise HTTPException(409,"Skupina s týmto názvom už existuje.")
        audit(db, actor["slug"], "group.create", {"name":data.name})
        return {"id":gid,"name":data.name,"members":0}
@app.patch("/auth/admin/groups/{gid}")
def admin_group_rename(gid:int, data:GroupIn, request:Request):
    actor=admin_only(request)
    with connect() as db:
        try: n=db.execute("UPDATE groups SET name=? WHERE id=?",(data.name,gid)).rowcount
        except sqlite3.IntegrityError: raise HTTPException(409,"Skupina s týmto názvom už existuje.")
        if not n: raise HTTPException(404,"Skupina neexistuje.")
        audit(db, actor["slug"], "group.rename", {"id":gid,"name":data.name})
        return {"id":gid,"name":data.name}
@app.delete("/auth/admin/groups/{gid}")
def admin_group_delete(gid:int, request:Request):
    actor=admin_only(request)
    with connect() as db:
        row=db.execute("SELECT name FROM groups WHERE id=?",(gid,)).fetchone()
        if not row: raise HTTPException(404,"Skupina neexistuje.")
        db.execute("DELETE FROM groups WHERE id=?",(gid,))
        audit(db, actor["slug"], "group.delete", {"id":gid,"name":row["name"]})
    return {"ok":True}
@app.get("/auth/admin/audit")
def admin_audit(request:Request):
    admin_only(request)
    with connect() as db:
        return [{**dict(r),"detail":json.loads(r["detail"])} for r in db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 100")]
@app.get("/auth/admin/settings")
def admin_settings(request:Request):
    admin_only(request)
    return {"smtp":smtp_ready(),"invite_hours":INVITE_HOURS,"apps":APPS}

# ---------------------------------------------------------------- interné rozhranie pre aplikácie

def internal_key(request):
    if not hmac.compare_digest(request.headers.get("x-sso-key",""),os.environ["SSO_INTERNAL_KEY"]): raise HTTPException(403)
@app.get("/internal/identity")
def internal(request:Request):
    internal_key(request)
    return identity(request)
@app.get("/internal/directory")
def directory(request:Request):
    """Všetky účty s prístupmi a skupinami: aplikácie si podľa neho zakladajú ľudí a filtrujú viditeľnosť."""
    internal_key(request)
    with connect() as db:
        groups=groups_of(db)
        out=[public_user(r,groups) for r in db.execute("SELECT * FROM users ORDER BY slug")]
    for u in out: u.pop("created_at")
    return out
app.mount("/static",StaticFiles(directory=os.path.join(os.path.dirname(__file__),"static")),name="static")
@app.get("/")
def index(): return FileResponse(os.path.join(os.path.dirname(__file__),"static/index.html"))

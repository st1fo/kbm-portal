import os, sqlite3, secrets, hashlib, hmac, time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

DB=os.getenv("PORTAL_DB", "/data/portal.sqlite")
COOKIE="kbm_portal_session"
PEOPLE=[("maros", "Maroš Sokolák", True),("zdenko", "Zdenko Sokolovský", False),("florian", "Florián Žifčák", False),("martin", "Martin Zelenák", False)]
ATTEMPTS=defaultdict(deque)
def connect():
    db=sqlite3.connect(DB, timeout=15); db.row_factory=sqlite3.Row; return db
def hashed(password, salt=None):
    salt=salt or secrets.token_hex(16)
    return salt+":"+hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1).hex()
def verify(password, stored): return hmac.compare_digest(hashed(password,stored.split(":")[0]),stored)
def digest(value): return hashlib.sha256(value.encode()).hexdigest()
DUMMY=hashed(secrets.token_urlsafe(32))
@asynccontextmanager
async def lifespan(app):
    if len(os.environ.get("SSO_INTERNAL_KEY", ""))<32: raise RuntimeError("SSO_INTERNAL_KEY is required")
    with connect() as db:
        db.executescript("CREATE TABLE IF NOT EXISTS users (slug TEXT PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, admin INTEGER NOT NULL, password TEXT NOT NULL); CREATE TABLE IF NOT EXISTS sessions (digest TEXT PRIMARY KEY, slug TEXT NOT NULL, expires REAL NOT NULL);")
        if not db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            password=os.environ.get("DEMO_PASSWORD", "")
            if len(password)<12: raise RuntimeError("DEMO_PASSWORD needs at least 12 characters")
            for slug,name,admin in PEOPLE:
                db.execute("INSERT INTO users VALUES (?,?,?,?,?)",(slug,name,slug+"@demo.kbm.local",admin,hashed(password)))
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
def identity(request):
    with connect() as db:
        user=db.execute("SELECT u.slug,u.name,u.email,u.admin FROM users u JOIN sessions s ON s.slug=u.slug WHERE s.digest=? AND s.expires>?",(digest(request.cookies.get(COOKIE,"")),time.time())).fetchone()
    if not user: raise HTTPException(401,"Prihláste sa do portálu.")
    return dict(user)
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
@app.post("/auth/login")
def login(data:Credentials, request:Request, response:Response):
    # Nginx overwrites this header; only the gateway is exposed.
    key=request.headers.get("x-real-ip",request.client.host)
    bucket=ATTEMPTS[key]; now=time.time()
    while bucket and bucket[0]<now-60: bucket.popleft()
    if len(bucket)>=10: raise HTTPException(429,"Príliš veľa pokusov. Skúste o minútu.")
    bucket.append(now)
    with connect() as db:
        value=data.username.strip().lower()
        user=db.execute("SELECT * FROM users WHERE slug=? OR email=?",(value,value)).fetchone()
        valid=verify(data.password,user["password"] if user else DUMMY)
        if not user or not valid: raise HTTPException(401,"Nesprávne prihlasovacie meno alebo heslo.")
        token=secrets.token_urlsafe(32)
        db.execute("DELETE FROM sessions WHERE expires<=? OR digest=?",(now,digest(request.cookies.get(COOKIE,""))))
        db.execute("INSERT INTO sessions VALUES (?,?,?)",(digest(token),user["slug"],now+43200))
    response.set_cookie(COOKIE,token,httponly=True,secure=os.getenv("COOKIE_SECURE","0")=="1",samesite="lax",max_age=43200,path="/")
    return {"ok":True}
@app.get("/auth/me")
def me(request:Request): return identity(request)
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
@app.get("/internal/identity")
def internal(request:Request):
    if not hmac.compare_digest(request.headers.get("x-sso-key",""),os.environ["SSO_INTERNAL_KEY"]): raise HTTPException(403)
    return identity(request)
app.mount("/static",StaticFiles(directory=os.path.join(os.path.dirname(__file__),"static")),name="static")
@app.get("/")
def index(): return FileResponse(os.path.join(os.path.dirname(__file__),"static/index.html"))

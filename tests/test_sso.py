"""Integration tests for central sessions and the two real application APIs.
Run: python -m pytest tests -q
Uses isolated SQLite databases; Docker deployment uses PostgreSQL for the apps.
"""
import os, pathlib, subprocess, sys, time, uuid
import httpx
import pytest

ROOT=pathlib.Path(__file__).resolve().parents[1]
PASSWORD='Integration-Test-Password-123'
KEY='a'*64

@pytest.fixture(scope='module')
def services(tmp_path_factory):
    data=tmp_path_factory.mktemp('sso')
    processes=[]
    common={**os.environ,'NO_PROXY':'127.0.0.1,localhost','no_proxy':'127.0.0.1,localhost','SSO_INTERNAL_KEY':KEY,'SSO_URL':'http://127.0.0.1:19100','JWT_SECRET':KEY,'DEMO_PASSWORD':PASSWORD,'COOKIE_SECURE':'0','PORTAL_DB':str(data/'portal.sqlite'),'PORTAL_DIRECTORY_CACHE_SECONDS':'0','WORKPLAN_INTERNAL_URL':'http://127.0.0.1:19101','LOGBOOK_INTERNAL_URL':'http://127.0.0.1:19102'}
    try:
        # Test schemas use ORM metadata because Workplan's frozen migration is PostgreSQL DDL.
        for app in ['workplan','vehicle-logbook']:
            directory=ROOT/'apps'/app/'backend'
            env={**common,'DATABASE_URL':'sqlite:///'+str(data/(app+'.sqlite'))}
            setup=('from app import models; from app.db import Base,engine; Base.metadata.create_all(engine); from app.db import SessionLocal; from app.init_data import init_reference; db=SessionLocal(); init_reference(db); db.commit(); db.close(); from app.portal_init import main; main()' if app=='workplan' else 'from app.db import Base,engine; Base.metadata.create_all(engine); from app.portal_init import main; main()')
            subprocess.run([sys.executable,'-c',setup],cwd=directory,env=env,check=True)
        for directory,port,module,env in [
            (ROOT/'portal',19100,'main:app',common),
            (ROOT/'apps/workplan/backend',19101,'app.main:app',{**common,'DATABASE_URL':'sqlite:///'+str(data/'workplan.sqlite')}),
            (ROOT/'apps/vehicle-logbook/backend',19102,'app.main:app',{**common,'DATABASE_URL':'sqlite:///'+str(data/'vehicle-logbook.sqlite')})]:
            processes.append(subprocess.Popen([sys.executable,'-m','uvicorn',module,'--host','127.0.0.1','--port',str(port)],cwd=directory,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE))
        with httpx.Client(trust_env=False) as client:
            for port,path in [(19100,'/auth/health'),(19101,'/api/health'),(19102,'/api/health')]:
                deadline=time.monotonic()+20
                while True:
                    try:
                        if client.get(f'http://127.0.0.1:{port}{path}').status_code==200: break
                    except httpx.TransportError: pass
                    if time.monotonic()>deadline: raise RuntimeError('API failed to start: '+str(port))
                    time.sleep(.1)
        yield {'portal':'http://127.0.0.1:19100','workplan':'http://127.0.0.1:19101','logbook':'http://127.0.0.1:19102'}
    finally:
        for process in processes: process.terminate()
        for process in processes:
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill()

def client(services): return httpx.Client(base_url=services['portal'],trust_env=False,headers={'X-Real-IP':str(uuid.uuid4())})
def login(c,username='maros',password=PASSWORD):
    response=c.post('/auth/login',json={'username':username,'password':password})
    assert response.status_code==200,response.text
    return dict(c.cookies)
def get(url,cookies=None):
    with httpx.Client(trust_env=False) as c: return c.get(url,cookies=cookies)

def test_requires_central_session(services):
    for name,path in [('portal','/auth/me'),('workplan','/api/auth/me'),('logbook','/api/me')]:
        assert get(services[name]+path).status_code==401
        assert get(services[name]+path,{'kbm_portal_session':'forged'}).status_code==401

def test_one_login_resolves_both_apps(services):
    with client(services) as c:
        cookies=login(c)
        assert c.get('/auth/me').json()['name']=='Maroš Sokolák'
        assert get(services['workplan']+'/api/auth/me',cookies).json()['email']=='maros@demo.kbm.local'
        assert get(services['logbook']+'/api/me',cookies).json()['admin'] is True
        assert get(services['workplan']+'/api/reports?start=2026-11-01&end=2026-11-30&format=xlsx',cookies).status_code==200
        assert get(services['logbook']+'/api/users',cookies).status_code==200
        assert get(services['workplan']+'/api/calendar?month=2026-11',cookies).status_code==200

def test_logout_revokes_copied_cookie_in_both_apps(services):
    with client(services) as c:
        cookies=login(c)
        assert c.post('/auth/logout').status_code==200
        for name,path in [('workplan','/api/auth/me'),('logbook','/api/me')]:
            assert get(services[name]+path,cookies).status_code==401

def test_employee_permissions(services):
    with client(services) as c:
        cookies=login(c,'zdenko@demo.kbm.local')
        assert get(services['workplan']+'/api/auth/me',cookies).json()['role']=='employee'
        assert get(services['logbook']+'/api/me',cookies).json()['admin'] is False
        assert get(services['logbook']+'/api/users',cookies).status_code==403
        assert get(services['logbook']+'/api/vehicles',cookies).json()==[]

def test_internal_identity_needs_service_key(services):
    with client(services) as c:
        login(c)
        assert c.get('/internal/identity').status_code==403
        assert c.get('/internal/identity',headers={'X-SSO-Key':KEY}).status_code==200

def test_cross_origin_writes_and_legacy_login_rejected(services):
    with httpx.Client(trust_env=False) as c:
        for name,path in [('portal','/auth/logout'),('workplan','/api/attendance'),('logbook','/api/logout')]:
            assert c.post(services[name]+path,headers={'Origin':'https://evil.example'}).status_code==403
        assert c.post(services['workplan']+'/api/auth/login',json={'email':'maros@demo.kbm.local','password':PASSWORD}).status_code==410
        assert c.post(services['logbook']+'/api/login',json={'username':'maros','password':PASSWORD}).status_code==410

def test_cookie_flags_and_session_rotation(services):
    with client(services) as c:
        response=c.post('/auth/login',json={'username':'martin','password':PASSWORD})
        cookie=response.headers['set-cookie'].lower()
        assert 'httponly' in cookie and 'samesite=lax' in cookie and 'path=/' in cookie
        old=dict(c.cookies)
        login(c,'martin')
        assert old!=dict(c.cookies)
        assert get(services['portal']+'/auth/me',old).status_code==401

def test_password_change_revokes_all_devices(services):
    with client(services) as c,client(services) as second:
        old=login(c,'florian');other=login(second,'florian')
        assert c.post('/auth/password',json={'current':PASSWORD,'password':'Changed-Integration-Password-456'}).status_code==200
        for cookie in [old,other]:
            assert get(services['portal']+'/auth/me',cookie).status_code==401
            assert get(services['workplan']+'/api/auth/me',cookie).status_code==401
            assert get(services['logbook']+'/api/me',cookie).status_code==401
        assert c.post('/auth/login',json={'username':'florian','password':PASSWORD}).status_code==401
        login(c,'florian','Changed-Integration-Password-456')

def test_logbook_export_with_shared_session(services):
    with client(services) as c,httpx.Client(trust_env=False) as book:
        cookies=login(c)
        book.cookies.update(cookies)
        response=book.post(services['logbook']+'/api/vehicles',json={'name':'SSO test','registration':'TEST-SSO','start_date':'2026-11-01','initial_odo':100,'capacity':50,'city_rate':7,'outside_rate':5})
        assert response.status_code==200,response.text
        vid=response.json()['id']
        for format in ['pdf','xlsx']:
            response=book.get(services['logbook']+f'/api/vehicles/{vid}/export?year=2026&month=11&format={format}&kind=report')
            assert response.status_code==200,response.text
            assert response.content.startswith(b'%PDF' if format=='pdf' else b'PK')

def test_login_attempts_are_limited(services):
    with client(services) as c:
        for _ in range(10):
            assert c.post('/auth/login',json={'username':'maros','password':'wrong-password'}).status_code==401
        assert c.post('/auth/login',json={'username':'maros','password':PASSWORD}).status_code==429


def test_admin_creates_user_with_app_access_groups_and_invite(services):
    with client(services) as admin,client(services) as emp:
        login(admin); login(emp,'zdenko')
        assert emp.get('/auth/admin/users').status_code==403
        groups={g['name']:g['id'] for g in admin.get('/auth/admin/groups').json()}
        assert {'Vedenie','Vývojári','Predaj','Technici','Administratíva','Brigádnici'}<=set(groups)
        r=admin.post('/auth/admin/users',json={'name':'Jana Nováková','username':'jana','email':'jana@test.local','apps':['vehicle-logbook'],'groups':[groups['Brigádnici']],'send_email':True},headers={'Origin':services['portal']})
        assert r.status_code==200,r.text
        body=r.json(); assert not body['emailed'] and 'SMTP' in body['email_error'] and '/?invite=' in body['invite_url']
        token=body['invite_url'].split('invite=')[1]
        assert admin.post('/auth/admin/users',json={'name':'Iná','username':'jana','email':'x@test.local'}).status_code==409
        # pozvánka: nastavenie hesla a prihlásenie
        assert get(services['portal']+'/auth/invite?token='+token).json()['username']=='jana'
        assert admin.post('/auth/invite',json={'token':token,'password':'Jana-Heslo-2026!'}).status_code==200
        assert admin.post('/auth/invite',json={'token':token,'password':'Jana-Heslo-2026!'}).status_code==404   # jednorazový
    with client(services) as jana:
        cookies=login(jana,'jana','Jana-Heslo-2026!')
        me=jana.get('/auth/me').json(); assert me['apps']==['vehicle-logbook'] and me['groups'][0]['name']=='Brigádnici'
        assert jana.get('/auth/app/vehicle-logbook').status_code==200 and jana.get('/auth/app/workplan').status_code==403
        assert get(services['logbook']+'/api/me',cookies).status_code==200
        assert get(services['workplan']+'/api/auth/me',cookies).status_code==403

def test_group_visibility_in_workplan_and_deactivation(services):
    with client(services) as admin:
        login(admin)
        groups={g['name']:g['id'] for g in admin.get('/auth/admin/groups').json()}
        r=admin.post('/auth/admin/users',json={'name':'Peter Predajca','username':'peter','email':'peter@test.local','apps':['workplan'],'groups':[groups['Predaj']],'send_email':False})
        token=r.json()['invite_url'].split('invite=')[1]
        admin.post('/auth/invite',json={'token':token,'password':'Peter-Heslo-2026!'})
    with client(services) as z, client(services) as p, client(services) as m:
        zc=login(z,'zdenko'); pc=login(p,'peter','Peter-Heslo-2026!'); mc=login(m)
        names=lambda c:{u['name'] for u in get(services['workplan']+'/api/employees',c).json()}
        assert 'Peter Predajca' not in names(zc) and {'Zdenko Sokolovský','Florián Žifčák','Martin Zelenák'}<=names(zc)
        assert names(pc)=={'Peter Predajca'}
        assert 'Peter Predajca' in names(mc) and 'Maroš Sokolák' in names(mc)          # admin vidí všetkých
        # Peter pridaný aj do Vývojárov → vidí sa so Zdenkom
        assert m.patch('/auth/admin/users/peter',json={'groups':[groups['Predaj'],groups['Vývojári']]},headers={'Origin':services['portal']}).status_code==200
        assert 'Peter Predajca' in names(zc)
        # deaktivácia zruší reláciu aj prihlásenie
        assert m.patch('/auth/admin/users/peter',json={'active':False}).status_code==200
        assert get(services['workplan']+'/api/auth/me',pc).status_code==401
        assert p.post('/auth/login',json={'username':'peter','password':'Peter-Heslo-2026!'}).status_code==401
        assert m.patch('/auth/admin/users/maros',json={'active':False}).status_code==422            # seba nie


def test_admin_permanently_deletes_user_with_all_app_data(services):
    with client(services) as admin:
        login(admin)
        r=admin.post('/auth/admin/users',json={'name':'Eva Dočasná','username':'eva','email':'eva@test.local','apps':['workplan','vehicle-logbook'],'send_email':False})
        admin.post('/auth/invite',json={'token':r.json()['invite_url'].split('invite=')[1],'password':'Eva-Heslo-2026!!'})
    with client(services) as eva, httpx.Client(trust_env=False) as app:
        cookies=login(eva,'eva','Eva-Heslo-2026!!'); app.cookies.update(cookies)
        me=app.get(services['workplan']+'/api/auth/me').json()
        assert app.post(services['workplan']+'/api/attendance',json={'user_id':me['id'],'type_id':1,'start_date':'2026-11-02','end_date':'2026-11-02'}).status_code==200
        assert app.put(services['workplan']+'/api/journal/days/2026-11-02',json={'segments':[{'start':'08:00','end':'12:00'}]}).status_code==200
        assert app.post(services['workplan']+'/api/journal/boards',json={'name':'Moje veci'}).status_code==200
        assert app.get(services['logbook']+'/api/me').status_code==200
    with client(services) as admin:
        login(admin)
        assert admin.request('DELETE','/auth/admin/users/eva',json={'confirm':'iny'}).status_code==422
        assert admin.request('DELETE','/auth/admin/users/maros',json={'confirm':'maros'}).status_code==422
        r=admin.request('DELETE','/auth/admin/users/eva',json={'confirm':'eva'})
        assert r.status_code==200,r.text
        res=r.json()['results']; assert res['workplan']['found'] and res['workplan']['counts']['plany']==1 and res['workplan']['counts']['nastenky']>=1
        assert res['vehicle-logbook']['found']
        assert 'eva' not in [u['slug'] for u in admin.get('/auth/admin/users').json()]
        assert any(a['action']=='user.delete' for a in admin.get('/auth/admin/audit').json())
        assert 'Eva Dočasná' not in [u['name'] for u in admin.get(services['workplan']+'/api/employees').json()]
        assert 'eva' not in [u['username'] for u in admin.get(services['logbook']+'/api/users').json()]
    assert get(services['workplan']+'/api/auth/me',cookies).status_code==401
    with client(services) as c:
        assert c.post('/auth/login',json={'username':'eva','password':'Eva-Heslo-2026!!'}).status_code==401
    # interné mazanie aplikácií nie je dostupné bez kľúča
    with httpx.Client(trust_env=False) as c:
        assert c.post(services['workplan']+'/api/internal/purge-user',json={'email':'zdenko@demo.kbm.local'}).status_code==403
        assert c.post(services['logbook']+'/api/internal/purge-user',json={'username':'zdenko'}).status_code==403

import os
os.environ.setdefault('JWT_SECRET','test-only-secret-with-at-least-32-characters')
os.environ.setdefault('DEMO_PASSWORD','Demo-test-only-2026')
import io
from datetime import date
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from app.db import Base, get_db
from app.models import User, AttendanceType, Attendance, LeaveRequest
from app.main import app, login_attempts
from app.security import hasher
from openpyxl import load_workbook

@pytest.fixture()
def client():
    url=os.getenv('TEST_DATABASE_URL','sqlite://')
    engine=create_engine(url,connect_args={'check_same_thread':False} if url=='sqlite://' else {},**({'poolclass':StaticPool} if url=='sqlite://' else {}))
    Base.metadata.drop_all(engine); Base.metadata.create_all(engine)
    with Session(engine) as db:
        for i in range(1,4):db.add(User(id=i,name='User '+str(i),email=f'u{i}@test.local',role='admin' if i==1 else 'employee',password_hash=hasher.hash('password-test-123')))
        db.add_all([AttendanceType(id=1,name='Office',icon='Building2',color='#123456'),AttendanceType(id=2,name='Home',icon='House',color='#123456'),AttendanceType(id=3,name='Vacation',icon='Palmtree',color='#123456',requires_approval=True)])
        db.commit()
    def override():
        with Session(engine) as db: yield db
    app.dependency_overrides[get_db]=override;login_attempts.clear()
    with TestClient(app) as c:yield c
    app.dependency_overrides.clear();engine.dispose()
def auth(c,i=1):
    r=c.post('/api/auth/login',json={'email':f'u{i}@test.local','password':'password-test-123'});assert r.status_code==200
    return {'Authorization':'Bearer '+r.json()['access_token']}
def plan(**kwargs):
    p={'user_id':2,'type_id':1,'start_date':'2026-11-02','end_date':'2026-11-02','start':'08:00','end':'16:30'};p.update(kwargs);return p

def test_authentication(client):
    assert client.get('/api/auth/me').status_code in (401,403)
    assert client.post('/api/auth/login',json={'email':'u2@test.local','password':'wrong'}).status_code==401
    assert client.get('/api/auth/me',headers=auth(client,2)).json()['id']==2
    assert client.get('/api/auth/me',headers={'Authorization':'Bearer broken'}).status_code==401

def test_permissions_and_admin_routes(client):
    h=auth(client,2)
    assert client.post('/api/attendance',headers=h,json=plan(user_id=3)).status_code==403
    for path in ['/api/audit','/api/reports?start=2026-11-01&end=2026-11-30']:
        assert client.get(path,headers=h).status_code==403
    assert client.post('/api/employees',headers=h,json={'name':'Name Test','email':'x@y.local','password':'longpassword'}).status_code==403

def test_attendance_create_update_delete(client):
    h=auth(client,2)
    assert client.post('/api/attendance',json=plan(),headers=h).status_code==200
    assert client.post('/api/attendance',json=plan(type_id=2,replace=True),headers=h).status_code==200
    rows=client.get('/api/calendar?month=2026-11',headers=h).json()['entries'];assert len(rows)==1 and rows[0]['type_id']==2
    assert client.delete('/api/attendance/'+str(rows[0]['id']),headers=auth(client,3)).status_code==403
    assert client.delete('/api/attendance/'+str(rows[0]['id']),headers=h).status_code==200

def test_range_and_conflicts_are_atomic(client):
    h=auth(client,2)
    assert client.post('/api/attendance',headers=h,json=plan(start_date='2026-11-03',end_date='2026-11-04')).status_code==200
    assert client.post('/api/attendance',headers=h,json=plan(end_date='2026-11-05')).status_code==409
    assert len(client.get('/api/calendar?month=2026-11',headers=h).json()['entries'])==2
    assert client.post('/api/attendance',headers=h,json=plan(end_date='2026-10-01')).status_code==422
    assert client.post('/api/attendance',headers=h,json=plan(start='17:00',end='08:00')).status_code==422

def test_leave_approval_and_notifications(client):
    employee=auth(client,2);admin=auth(client)
    assert client.post('/api/attendance',headers=employee,json=plan(type_id=3)).status_code==403
    client.post('/api/attendance',headers=employee,json=plan())
    r=client.post('/api/requests',headers=employee,json=plan(type_id=3,end_date='2026-11-08'));assert r.status_code==200
    rid=r.json()['id']
    assert client.post('/api/requests',headers=employee,json=plan(type_id=3)).status_code==409
    assert client.post(f'/api/requests/{rid}/review',headers=employee,json={'status':'approved'}).status_code==403
    assert client.post(f'/api/requests/{rid}/review',headers=admin,json={'status':'approved'}).status_code==200
    rows=client.get('/api/calendar?month=2026-11',headers=employee).json()['entries'];assert len(rows)==5 and all(x['type_id']==3 for x in rows)
    assert client.post(f'/api/requests/{rid}/review',headers=admin,json={'status':'rejected'}).status_code==409
    assert client.delete('/api/attendance/'+str(rows[0]['id']),headers=employee).status_code==409
    assert client.delete(f'/api/requests/{rid}',headers=employee).status_code==403
    assert client.get('/api/notifications',headers=employee).json()
    assert client.delete(f'/api/requests/{rid}',headers=admin).status_code==200
    assert not client.get('/api/calendar?month=2026-11',headers=employee).json()['entries']

def test_admin_cannot_approve_own_leave(client):
    h=auth(client);r=client.post('/api/requests',headers=h,json=plan(user_id=1,type_id=3));rid=r.json()['id']
    assert client.post(f'/api/requests/{rid}/review',headers=h,json={'status':'approved'}).status_code==403

def test_leave_rejection_and_history(client):
    h=auth(client,2);r=client.post('/api/requests',headers=h,json=plan(type_id=3));rid=r.json()['id']
    result=client.post(f'/api/requests/{rid}/review',headers=auth(client),json={'status':'rejected','reason':'Test reason'})
    assert result.json()['reviewed_by']==1 and result.json()['reason']=='Test reason'
    assert not client.get('/api/calendar?month=2026-11',headers=h).json()['entries']
    assert len(client.get('/api/requests',headers=auth(client,3)).json())==0

def test_month_filter_holidays_and_settings(client):
    h=auth(client)
    assert client.put('/api/holidays',headers=h,json={'date':'2026-11-03','name':'Company day'}).status_code==200
    assert client.put('/api/settings',headers=h,json={'daily_hours':7.5,'weekends':[5,6]}).status_code==200
    r=client.post('/api/requests',headers=auth(client,2),json=plan(type_id=3,end_date='2026-11-04'))
    client.post('/api/requests/'+str(r.json()['id'])+'/review',headers=h,json={'status':'approved'})
    assert len(client.get('/api/calendar?month=2026-11',headers=h).json()['entries'])==2
    assert client.get('/api/calendar?month=2026-12',headers=h).json()['entries']==[]
    assert client.get('/api/calendar?month=nonsense',headers=h).status_code==422

def test_reports_and_formula_injection(client):
    h=auth(client);client.post('/api/attendance',headers=h,json=plan(note='=HYPERLINK("test")'))
    uri='/api/reports?start=2026-11-01&end=2026-11-30'
    r=client.get(uri,headers=h);assert r.status_code==200 and "'=HYPERLINK" in r.text
    r=client.get(uri+'&format=xlsx',headers=h);wb=load_workbook(io.BytesIO(r.content));assert wb.active['D2'].data_type=='s'

def test_employee_deactivation(client):
    h=auth(client);employee=auth(client,2)
    p={'name':'User 2','email':'u2@test.local','role':'employee','active':False,'daily_hours':8}
    assert client.put('/api/employees/2',headers=h,json=p).status_code==200
    assert client.get('/api/auth/me',headers=employee).status_code==401
    assert client.put('/api/employees/1',headers=h,json={**p,'email':'u1@test.local'}).status_code==422

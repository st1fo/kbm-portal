from datetime import datetime
import pytest
from test_workplan import client, auth  # noqa: F401 (fixture)
from app import journal
from app.common import TZ

ADMIN, EMP, OTHER = 1, 2, 3


@pytest.fixture()
def clock(monkeypatch):
    """Pevný „teraz“ pre server; posúva sa cez clock.set('2026-11-05 08:00')."""
    class Clock:
        value = datetime(2026, 11, 5, 8, 0, tzinfo=TZ)
        def set(self, s): self.value = datetime.fromisoformat(s).replace(tzinfo=TZ)
    c = Clock()
    monkeypatch.setattr(journal, 'now', lambda: c.value)
    return c


def punch(c, kind, when, cid, i=EMP):
    return c.post('/api/journal/punch', headers=auth(c, i), json={'client_id': cid, 'kind': kind, 'at': when + ':00+01:00'})


def audit_actions(c):
    return [a['action'] for a in c.get('/api/audit', headers=auth(c)).json()]


def test_punch_day_flow_and_idempotency(client, clock):
    assert client.get('/api/journal/state', headers=auth(client, EMP)).json()['status'] == 'out'
    r = punch(client, 'in', '2026-11-05T08:00', 'punch-0001')
    assert r.status_code == 200 and r.json()['result'] == 'applied' and r.json()['state']['status'] == 'in'
    # tá istá požiadavka z offline fronty znova → nič nové
    r = punch(client, 'in', '2026-11-05T08:00', 'punch-0001')
    assert r.json()['duplicate'] and len(r.json()['state']['day']['segments']) == 1
    clock.set('2026-11-05 12:00'); punch(client, 'break', '2026-11-05T12:00', 'punch-0002')
    clock.set('2026-11-05 12:45')
    st = client.get('/api/journal/state', headers=auth(client, EMP)).json()
    assert st['status'] == 'break' and st['day']['break_running'] and st['day']['break_min'] == 45
    punch(client, 'in', '2026-11-05T12:30', 'punch-0003')
    clock.set('2026-11-05 16:30')
    day = punch(client, 'out', '2026-11-05T16:30', 'punch-0004').json()['state']['day']
    assert (day['worked_min'], day['break_min'], day['first'], day['last'], day['done']) == (480, 30, '08:00', '16:30', True)
    assert punch(client, 'out', '2026-11-05T16:31', 'punch-0005').json()['result'] == 'noop'
    assert punch(client, 'in', '2026-11-05T16:00', 'punch-0006').status_code == 409   # skorší ako posledný odchod


def test_punch_time_limits(client, clock):
    assert punch(client, 'in', '2026-11-05T08:10', 'future-01').status_code == 422      # 10 min v budúcnosti
    assert punch(client, 'in', '2026-10-20T08:00', 'tooold-01').status_code == 422
    r = client.post('/api/journal/punch', headers=auth(client, EMP), json={'client_id': 'naive-0001', 'kind': 'in', 'at': '2026-11-05T08:00:00'})
    assert r.status_code == 422


def test_overnight_shift_via_api(client, clock):
    clock.set('2026-11-04 22:00'); punch(client, 'in', '2026-11-04T22:00', 'night-001')
    clock.set('2026-11-05 03:00')
    st = client.get('/api/journal/state', headers=auth(client, EMP)).json()
    assert st['active_day'] == '2026-11-04' and st['day']['worked_min'] == 300
    st = punch(client, 'out', '2026-11-05T02:30', 'night-002').json()['state']
    assert st['active_day'] == '2026-11-05' and st['status'] == 'out'      # uzavretá nočná zmena už nie je aktívna
    day = client.get('/api/journal/days/2026-11-04', headers=auth(client, EMP)).json()
    assert day['date'] == '2026-11-04' and day['segments'][0]['end_next_day'] and day['last_next_day'] and day['worked_min'] == 270


def test_manual_day_edit_validation_and_permissions(client, clock):
    h = auth(client, EMP)
    r = client.put('/api/journal/days/2026-11-03', headers=h, json={'segments': [{'start': '08:00', 'end': None}, {'start': '13:00', 'end': '15:00'}]})
    assert r.status_code == 422 and 'úsek 1 nemá čas odchodu' in r.json()['detail']
    r = client.put('/api/journal/days/2026-11-03', headers=h, json={'segments': [{'start': '08:00', 'end': '12:00'}, {'start': '12:30', 'end': '16:00'}, {'start': '', 'end': ''}]})
    assert r.status_code == 200 and r.json()['done'] and r.json()['worked_min'] == 450 and len(r.json()['segments']) == 2
    assert [d['date'] for d in client.get('/api/journal/days?month=2026-11', headers=h).json()] == ['2026-11-03']
    # cudzí zamestnanec nevidí ani nemení
    assert client.get('/api/journal/days?month=2026-11&user_id=2', headers=auth(client, OTHER)).status_code == 403
    assert client.put('/api/journal/days/2026-11-03?user_id=2', headers=auth(client, OTHER), json={'segments': []}).status_code == 403
    # admin opraví a zapíše sa audit
    r = client.put('/api/journal/days/2026-11-03?user_id=2', headers=auth(client), json={'segments': [{'start': '07:30', 'end': '16:00'}]})
    assert r.status_code == 200 and r.json()['worked_min'] == 510
    assert 'journal.day.save' in audit_actions(client)
    assert client.delete('/api/journal/days/2026-11-03', headers=h).status_code == 200
    assert client.delete('/api/journal/days/2026-11-03', headers=h).status_code == 404
    assert client.get('/api/journal/days?month=2026-11', headers=h).json() == []


def test_catalog_dedupe_and_delete_rules(client, clock):
    h, a = auth(client, EMP), auth(client)
    c1 = client.post('/api/journal/customers', headers=h, json={'name': ' Žilinská  univerzita '}).json()
    c2 = client.post('/api/journal/customers', headers=h, json={'name': 'žilinská univerzita'}).json()
    assert c1['id'] == c2['id'] and c1['name'] == 'Žilinská univerzita'
    p1 = client.post('/api/journal/projects', headers=h, json={'name': 'Infraštruktúra', 'customer_name': 'ŽILINSKÁ UNIVERZITA'}).json()
    p2 = client.post('/api/journal/projects', headers=h, json={'name': 'infraštruktúra', 'customer_id': c1['id']}).json()
    p3 = client.post('/api/journal/projects', headers=h, json={'name': 'Infraštruktúra'}).json()      # bez zákazníka = iný projekt
    assert p1['id'] == p2['id'] != p3['id'] and p1['customer'] == 'Žilinská univerzita'
    t = client.post('/api/journal/tasks', headers=h, json={'name': 'Migrácia VM', 'project_id': p1['id']}).json()
    loose = client.post('/api/journal/tasks', headers=h, json={'name': 'Telefonát', 'customer_name': 'Nový zákazník'}).json()
    assert t['customer'] == 'Žilinská univerzita' and loose['project_id'] is None and loose['customer'] == 'Nový zákazník'
    with pytest.raises(Exception): client.post('/api/journal/tasks', headers=h, json={'name': ''}).raise_for_status()
    # zamestnanec upravuje, ale nemaže ani nedeaktivuje
    assert client.patch(f"/api/journal/tasks/{t['id']}", headers=h, json={'done': True}).json()['done']
    assert client.patch(f"/api/journal/tasks/{t['id']}", headers=h, json={'active': False}).status_code == 403
    assert client.delete(f"/api/journal/tasks/{t['id']}", headers=h).status_code == 403
    client.post('/api/journal/activities', headers=h, json={'task_id': t['id'], 'date': '2026-11-05', 'start': '08:00', 'end': '09:00', 'text': 'x'})
    assert client.delete(f"/api/journal/tasks/{t['id']}", headers=a).status_code == 409                  # má aktivity
    assert client.patch(f"/api/journal/tasks/{t['id']}", headers=a, json={'active': False}).json()['active'] is False
    assert client.delete(f"/api/journal/projects/{p1['id']}", headers=a).status_code == 409               # má úlohy
    assert client.delete(f"/api/journal/projects/{p3['id']}", headers=a).status_code == 200
    assert client.delete(f"/api/journal/customers/{c1['id']}", headers=a).status_code == 409
    # úloha bez projektu: zmena zákazníka a presun do projektu
    moved = client.patch(f"/api/journal/tasks/{loose['id']}", headers=h, json={'project_id': p1['id']}).json()
    assert moved['project_id'] == p1['id'] and moved['customer_id'] is None
    actions = audit_actions(client)
    assert 'journal.task.deactivate' in actions and 'journal.project.delete' in actions
    names = [x['name'] for x in client.get('/api/journal/tasks', headers=h).json()]
    assert 'Migrácia VM' not in names and 'Migrácia VM' in [x['name'] for x in client.get('/api/journal/tasks?include_inactive=true', headers=h).json()]


def test_activities_span_overnight_and_ownership(client, clock):
    h = auth(client, EMP)
    tid = client.post('/api/journal/tasks', headers=h, json={'name': 'Nočná údržba'}).json()['id']
    a = client.post('/api/journal/activities', headers=h, json={'task_id': tid, 'date': '2026-11-04', 'start': '23:00', 'end': '01:30', 'text': '– nasadil monitoring'}).json()
    assert (a['date'], a['start'], a['end'], a['end_next_day'], a['minutes']) == ('2026-11-04', '23:00', '01:30', True, 150)
    now_a = client.post('/api/journal/activities', headers=h, json={'task_id': tid, 'date': '2026-11-05', 'text': 'bez času'}).json()
    assert (now_a['start'], now_a['minutes'], now_a['end']) == ('08:00', None, None)
    r = client.post('/api/journal/activities', headers=h, json={'task_id': tid, 'date': '2026-11-05', 'end': '10:00', 'text': 'x'})
    assert r.status_code == 422
    # úprava bez „od“ zachová pôvodný čas začiatku
    e = client.put(f"/api/journal/activities/{a['id']}", headers=h, json={'task_id': tid, 'date': '2026-11-03', 'text': 'upravené'}).json()
    assert (e['date'], e['start'], e['minutes']) == ('2026-11-03', '23:00', None)
    assert [x['id'] for x in client.get('/api/journal/activities?start=2026-11-05&end=2026-11-05', headers=h).json()] == [now_a['id']]
    assert len(client.get('/api/journal/activities?q=UPRAVEN', headers=h).json()) == 1
    assert client.delete(f"/api/journal/activities/{a['id']}", headers=auth(client, OTHER)).status_code == 403
    assert client.get('/api/journal/activities?user_id=2', headers=auth(client, OTHER)).status_code == 403
    assert len(client.get('/api/journal/activities?user_id=2', headers=auth(client)).json()) == 2
    assert client.get('/api/journal/tasks', headers=h).json()[0]['activity_count'] == 2
    assert client.delete(f"/api/journal/activities/{a['id']}", headers=auth(client)).status_code == 200
    assert 'journal.activity.delete' in audit_actions(client)


def test_todos(client, clock):
    h = auth(client, EMP)
    pid = client.post('/api/journal/projects', headers=h, json={'name': 'Web'}).json()['id']
    t = client.post('/api/journal/todos', headers=h, json={'text': 'Zavolať dodávateľovi', 'due': '2026-11-06', 'priority': 2, 'customer_name': 'ACME'}).json()
    assert t['customer'] == 'ACME' and t['priority'] == 2 and not t['done']
    t2 = client.post('/api/journal/todos', headers=h, json={'text': 'Nasadiť', 'project_id': pid}).json()
    assert client.post('/api/journal/todos', headers=h, json={'text': 'x', 'priority': 3}).status_code == 422
    d = client.patch(f"/api/journal/todos/{t['id']}", headers=h, json={'done': True, 'due': None, 'project_id': pid}).json()
    assert d['done'] and d['done_at'].startswith('2026-11-05T08:00') and d['due'] is None and d['project'] == 'Web' and d['customer'] == ''
    assert client.patch(f"/api/journal/todos/{t2['id']}", headers=auth(client, OTHER), json={'done': True}).status_code == 403
    assert client.post('/api/journal/todos/clear-done', headers=h).json() == {'deleted': 1}
    assert [x['text'] for x in client.get('/api/journal/todos', headers=h).json()] == ['Nasadiť']
    assert client.get('/api/journal/todos', headers=auth(client, OTHER)).json() == []

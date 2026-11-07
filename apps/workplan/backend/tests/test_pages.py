"""Viditeľnosť podstránok: skupiny, jednotlivci, admin, blokovanie API."""
from test_workplan import client, auth, DIRECTORY, person, plan  # noqa: F401 (fixture)

ALL = ['calendar', 'journal', 'dashboard', 'requests']


def pages(c, i):
    return c.get('/api/auth/me', headers=auth(c, i)).json()['pages']


def test_page_access_groups_users_and_api(client):
    a = auth(client)
    assert pages(client, 2) == ALL
    assert client.get('/api/page-access', headers=auth(client, 2)).status_code == 403
    o = client.get('/api/page-access', headers=a).json()
    assert [g['name'] for g in o['groups']] == ['Skupina 1'] and o['groups'][0]['members'] == 3
    # skupina 1 vidí iba Denník; u3 má vlastné nastavenie
    r = client.put('/api/page-access', headers=a, json={'groups': {'1': ['journal']}, 'users': {'3': ['requests', 'calendar']}})
    assert r.status_code == 200
    assert next(u for u in r.json()['users'] if u['id'] == 2)['effective'] == ['journal']
    assert pages(client, 2) == ['journal'] and pages(client, 3) == ['calendar', 'requests'] and pages(client, 1) == ALL
    h2 = auth(client, 2)
    assert client.get('/api/calendar?month=2026-11', headers=h2).status_code == 403
    assert client.get('/api/statistics?month=2026-11', headers=h2).status_code == 403
    assert client.post('/api/attendance', headers=h2, json=plan()).status_code == 403
    assert client.post('/api/requests', headers=h2, json=plan(type_id=3)).status_code == 403
    assert client.get('/api/journal/state', headers=h2).status_code == 200
    assert client.get('/api/requests', headers=h2).status_code == 200            # zoznam (notifikácie) ostáva
    h3 = auth(client, 3)
    assert client.get('/api/journal/state', headers=h3).status_code == 403
    assert client.get('/api/calendar?month=2026-11', headers=h3).status_code == 200
    assert client.get('/api/calendar?month=2026-11', headers=a).status_code == 200  # admin vždy
    # človek v dvoch skupinách: druhá skupina bez nastavenia → vidí všetko; s nastavením → zjednotenie
    DIRECTORY[1] = person(2, groups=(1, 2))
    assert pages(client, 2) == ALL
    client.put('/api/page-access', headers=a, json={'groups': {'1': ['journal'], '2': ['dashboard']}})
    assert pages(client, 2) == ['journal', 'dashboard'] and pages(client, 3) == ['journal']   # u3 už nemá vlastné, platí skupina 1
    # validácia a audit
    assert client.put('/api/page-access', headers=a, json={'groups': {'1': []}}).status_code == 422
    assert client.put('/api/page-access', headers=a, json={'groups': {'1': ['admin']}}).status_code == 422
    assert 'pages.update' in [x['action'] for x in client.get('/api/audit', headers=a).json()]
    client.put('/api/page-access', headers=a, json={})
    assert pages(client, 2) == ALL

from datetime import datetime
import pytest
from test_workplan import client, auth  # noqa: F401 (fixture)
from test_journal_api import clock  # noqa: F401 (fixture)
from app.common import TZ

EMP, OTHER = 2, 3
def ms(s): return int(datetime.fromisoformat(s).replace(tzinfo=TZ).timestamp() * 1000)


def backup():
    """Záloha vo formáte tlačidla „Exportovať zálohu (JSON)“ (version 4) so zmesou starých záznamov."""
    return {'app': 'pracovny-dennik', 'version': 4, 'exportedAt': '2026-11-05T07:00:00.000Z', 'data': {
        'presence': {'status': 'out', 'since': None, 'segments': []},
        'projects': [{'id': 'p1', 'name': 'Infraštruktúra', 'customer': 'Žilinská univerzita'},
                     {'id': 'p2', 'name': 'Web', 'customer': ''},
                     {'id': 'p3', 'name': '  infraštruktúra ', 'customer': 'ŽILINSKÁ univerzita'}],   # duplicita p1
        'tasks': [{'id': 't1', 'name': 'Migrácia VM', 'projectId': 'p1', 'customer': '', 'done': False},
                  {'id': 't2', 'name': 'Telefonát', 'projectId': None, 'customer': 'ACME', 'done': True},
                  {'id': 't3', 'name': 'Príklad: prevzatie serverov', 'projectId': 'p1', 'customer': '', 'done': False}],
        'activities': [
            {'id': 'a1', 'ts': ms('2026-11-03T23:00'), 'end': ms('2026-11-04T01:30'), 'taskId': 't1', 'text': 'nočná migrácia', 'minutes': 150},
            {'id': 'a2', 'ts': ms('2026-11-04T10:15'), 'taskId': 't2', 'text': 'bez trvania', 'minutes': None},
            {'id': 'a3', 'ts': ms('2026-11-04T11:00'), 'taskId': 'zmazana', 'text': 'osirotená', 'minutes': 20},
            {'id': 'a4', 'ts': ms('2026-11-01T09:00'), 'taskId': 't3', 'text': 'Ukážková aktivita — projekt aj úlohu môžeš zmazať.', 'minutes': None}],
        'todos': [{'id': 'd1', 'text': 'Zavolať', 'projectId': None, 'customer': 'ACME', 'due': '2026-11-06', 'prio': 2, 'done': False, 'doneAt': None, 'createdAt': ms('2026-11-02T08:00')},
                  {'id': 'd2', 'text': 'Nasadiť', 'projectId': 'p2', 'customer': '', 'due': None, 'prio': 0, 'done': True, 'doneAt': ms('2026-11-04T15:00'), 'createdAt': ms('2026-11-02T09:00')}],
        'attendance': {
            '2026-11-03': {'segs': [{'in': '08:00', 'out': '12:00'}, {'in': '12:30', 'out': '16:30'}], 'done': True},
            '2026-11-02': {'arrival': '07:45', 'breakStart': '11:30', 'breakEnd': '12:00', 'departure': '16:00'},   # starý formát
            '2026-10-31': {'segs': [{'in': '22:00', 'out': '02:00'}], 'done': True},                                # cez polnoc
            '2026-10-30': {'segs': [{'in': None, 'out': '10:00'}], 'done': True},                                   # neplatný
            'nonsense': {'segs': [{'in': '08:00', 'out': '09:00'}]}},
    }}


def imp(c, payload, dry=False, i=EMP):
    return c.post('/api/journal/import?dry_run=' + ('true' if dry else 'false'), headers=auth(c, i), json=payload)


def test_preview_then_import_then_idempotent(client, clock):
    h = auth(client, EMP)
    preview = imp(client, backup(), dry=True)
    assert preview.status_code == 200, preview.text
    p = preview.json()
    assert p['dry_run'] and p['created'] == {'days': 3, 'segments': 5, 'customers': 2, 'projects': 2, 'tasks': 2, 'activities': 3, 'todos': 2}
    assert p['skipped']['projects'] == 1                       # p3 = p1 (názov aj zákazník bez ohľadu na veľkosť písmen)
    assert any('30.10.2026' in w for w in p['warnings']) and any('nonsense' in w for w in p['warnings'])
    # náhľad nič nezapísal
    assert client.get('/api/journal/projects', headers=h).json() == []
    assert client.get('/api/journal/days?month=2026-11', headers=h).json() == []

    r = imp(client, backup()).json()
    assert r['created'] == p['created'] and not r['dry_run']
    again = imp(client, backup()).json()
    assert sum(again['created'].values()) == 0
    assert again['skipped'] == {'days': 3, 'segments': 5, 'customers': 2, 'projects': 3, 'tasks': 2, 'activities': 3, 'todos': 2}

    # obsah
    days = {d['date']: d for d in client.get('/api/journal/days?month=2026-11', headers=h).json()}
    assert days['2026-11-02']['worked_min'] == 465 and days['2026-11-02']['break_min'] == 30
    assert days['2026-11-03']['worked_min'] == 480 and days['2026-11-03']['done']
    oct31 = client.get('/api/journal/days/2026-10-31', headers=h).json()
    assert oct31['segments'][0]['end_next_day'] and oct31['worked_min'] == 240
    acts = {a['text']: a for a in client.get('/api/journal/activities', headers=h).json()}
    assert set(acts) == {'nočná migrácia', 'bez trvania', 'osirotená'}
    n = acts['nočná migrácia']
    assert (n['date'], n['start'], n['end'], n['end_next_day'], n['minutes'], n['project'], n['customer']) == ('2026-11-03', '23:00', '01:30', True, 150, 'Infraštruktúra', 'Žilinská univerzita')
    assert acts['osirotená']['task_id'] is None and acts['osirotená']['minutes'] == 20
    tasks = {t['name']: t for t in client.get('/api/journal/tasks', headers=h).json()}
    assert set(tasks) == {'Migrácia VM', 'Telefonát'} and tasks['Telefonát']['done'] and tasks['Telefonát']['customer'] == 'ACME'
    board = client.get('/api/journal/boards', headers=h).json()[0]
    full = client.get(f"/api/journal/boards/{board['id']}", headers=h).json()
    cols = {c['id']: c for c in full['columns']}
    todos = {t['title']: t for t in full['cards']}
    assert todos['Zavolať']['priority'] == 2 and todos['Zavolať']['customer'] == 'ACME' and todos['Zavolať']['due'] == '2026-11-06'
    assert not cols[todos['Zavolať']['column_id']]['is_done'] and cols[todos['Nasadiť']['column_id']]['is_done']
    assert todos['Nasadiť']['done_at'].startswith('2026-11-04T15:00') and todos['Nasadiť']['project'] == 'Web'
    assert 'journal.import' in [a['action'] for a in client.get('/api/audit', headers=auth(client)).json()]


def test_existing_day_is_not_overwritten_and_catalog_is_shared(client, clock):
    client.put('/api/journal/days/2026-11-03', headers=auth(client, EMP), json={'segments': [{'start': '09:00', 'end': '10:00'}]})
    r = imp(client, backup()).json()
    assert r['created']['days'] == 2 and r['skipped']['days'] == 1
    assert client.get('/api/journal/days/2026-11-03', headers=auth(client, EMP)).json()['worked_min'] == 60
    # iný zamestnanec s tou istou zálohou: zákazníci, projekty aj úlohy sa zdieľajú, osobné dáta pribudnú jemu
    other = imp(client, backup(), i=OTHER).json()
    assert other['created']['customers'] == other['created']['projects'] == other['created']['tasks'] == 0
    assert other['created']['activities'] == 3 and other['created']['days'] == 3
    assert len(client.get('/api/journal/projects', headers=auth(client, OTHER)).json()) == 2


def test_v2_format_and_oldest_presence_segments(client, clock):
    v2 = {'tasks': [{'id': 'x1', 'name': 'Servis', 'project': 'Údržba', 'customer': 'Mesto', 'done': False,
                     'activities': [{'id': 'v1', 'ts': ms('2026-11-04T09:00'), 'text': 'výmena\x00 disku', 'minutes': 45}]},
                    {'name': 'Bez ID', 'project': '', 'customer': 'Mesto', 'activities': [{'ts': ms('2026-11-04T13:00'), 'text': 'konzultácia'}]}],
          'presence': {'status': 'out', 'segments': [{'start': ms('2026-11-02T08:00'), 'end': ms('2026-11-02T12:00')},
                                                      {'start': ms('2026-11-02T12:30'), 'end': ms('2026-11-02T16:00')}]}}
    r = imp(client, v2).json()
    assert r['created'] == {'days': 1, 'segments': 2, 'customers': 1, 'projects': 1, 'tasks': 2, 'activities': 2, 'todos': 0}
    assert sum(imp(client, v2).json()['created'].values()) == 0
    day = client.get('/api/journal/days/2026-11-02', headers=auth(client, EMP)).json()
    assert [(s['start'], s['end']) for s in day['segments']] == [('08:00', '12:00'), ('12:30', '16:00')]
    acts = client.get('/api/journal/activities', headers=auth(client, EMP)).json()
    assert {(a['text'], a['project'], a['customer'], a['minutes']) for a in acts} == {('výmena disku', 'Údržba', 'Mesto', 45), ('konzultácia', '', 'Mesto', None)}


@pytest.mark.parametrize('payload', [{'foo': 1}, {'data': {'tasks': 'x'}}, {'tasks': [], 'activities': 'x'}])
def test_rejects_unknown_files(client, clock, payload):
    assert imp(client, payload).status_code == 422


def test_rejects_large_upload(client, clock):
    big = {'tasks': [], 'activities': [], 'pad': 'x' * (5 * 1024 * 1024 + 10)}
    assert imp(client, big).status_code == 413

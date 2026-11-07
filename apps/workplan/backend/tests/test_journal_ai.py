"""AI doplnenie dňa: voľný čas, kontrola návrhu, zápis aktivít a nových úloh (Gemini je nahradený)."""
import pytest
from test_workplan import client, auth  # noqa: F401 (fixture)
from test_journal_api import clock, punch  # noqa: F401 (fixture)
from app import gemini

EMP, OTHER = 2, 3
A = '/api/journal/ai'


@pytest.fixture()
def fake(monkeypatch):
    calls = []
    def propose(context, history, message):
        calls.append((context, history, message))
        return gemini.Proposal.model_validate(fake.reply)
    monkeypatch.setattr(gemini, 'propose', propose)
    monkeypatch.setattr(gemini, 'configured', lambda: True)
    fake.calls = calls
    return fake


def test_day_propose_apply(client, clock, fake):
    h = auth(client, EMP)
    client.put('/api/journal/days/2026-11-04', headers=h, json={'segments': [{'start': '08:00', 'end': '12:00'}, {'start': '12:30', 'end': '16:30'}]})
    pid = client.post('/api/journal/projects', headers=h, json={'name': 'Web'}).json()['id']
    tid = client.post('/api/journal/tasks', headers=h, json={'name': 'nasadenie', 'project_id': pid}).json()['id']
    client.post('/api/journal/activities', headers=h, json={'task_id': tid, 'date': '2026-11-04', 'start': '08:00', 'end': '09:00', 'text': 'ráno'})
    d = client.get(f'{A}/day?date=2026-11-04', headers=h).json()
    assert d['worked_min'] == 480 and d['logged_min'] == 60 and d['free_min'] == 420
    assert [(f['start'], f['end']) for f in d['free']] == [('09:00', '12:00'), ('12:30', '16:30')]
    fake.reply = {'reply': 'Rozumiem.', 'items': [
        {'start': '09:00', 'end': '12:00', 'task_id': tid, 'text': '- nasadenie na server'},
        {'start': '12:30', 'end': '16:30', 'new_task': 'tmavý režim', 'project_id': pid, 'text': '- tmavý režim'},
        {'start': '25:00', 'end': '26:00', 'text': 'zlý čas'}]}
    r = client.post(f'{A}/propose', headers=h, json={'date': '2026-11-04', 'message': 'robil som nasadenie a tmavý režim'}).json()
    ctx = fake.calls[0][0]
    assert ctx['volny_cas'][0]['start'] == '09:00' and any(t['id'] == tid for t in ctx['ulohy'])
    assert len(r['items']) == 2 and all(not i['errors'] for i in r['items'])
    assert r['items'][0]['task'] == 'nasadenie' and r['items'][1]['project'] == 'Web' and r['items'][1]['minutes'] == 240
    # kontrola: prekryv, mimo pípnutí, chýbajúca úloha
    bad = client.post(f'{A}/check', headers=h, json={'date': '2026-11-04', 'items': [
        {'start': '08:30', 'end': '09:30', 'task_id': tid, 'text': 'x'},
        {'start': '17:00', 'end': '18:00', 'new_task': 'y', 'text': 'y'},
        {'start': '12:00', 'end': '12:20', 'text': 'z'}]}).json()
    assert 'Prekrýva sa s už zapísanou aktivitou.' in bad[0]['errors']
    assert bad[1]['warnings'] == ['Mimo odpracovaného času (pípnutí).'] and not bad[1]['errors']
    assert 'Vyberte úlohu alebo zadajte novú.' in bad[2]['errors']
    assert client.post(f'{A}/apply', headers=h, json={'date': '2026-11-04', 'items': [bad[0]]}).status_code == 422
    # zápis; nová úloha sa založí raz, druhý riadok s rovnakým názvom ju použije
    items = [{k: i[k] for k in ('start', 'end', 'task_id', 'new_task', 'project_id', 'customer', 'text')} for i in r['items']]
    items.append({'start': '16:30', 'end': '16:45', 'new_task': 'Tmavý režim', 'project_id': pid, 'text': '- dokončenie'})
    ok = client.post(f'{A}/apply', headers=h, json={'date': '2026-11-04', 'items': items}).json()
    assert ok == {'created': 3, 'new_tasks': ['tmavý režim']}
    acts = client.get('/api/journal/activities?start=2026-11-04&end=2026-11-04', headers=h).json()
    assert sorted((a['start'], a['end'], a['task'], a['minutes']) for a in acts) == [
        ('08:00', '09:00', 'nasadenie', 60), ('09:00', '12:00', 'nasadenie', 180), ('12:30', '16:30', 'tmavý režim', 240), ('16:30', '16:45', 'tmavý režim', 15)]
    assert client.get(f'{A}/day?date=2026-11-04', headers=h).json()['free_min'] == 0
    # cudzí denník nie; druhé kolo posiela históriu a rozpracovaný návrh
    assert client.post(f'{A}/propose?user_id={EMP}', headers=auth(client, OTHER), json={'date': '2026-11-04', 'message': 'x'}).status_code == 403
    client.post(f'{A}/propose', headers=h, json={'date': '2026-11-04', 'message': 'call daj na 15 minút',
                                                 'history': [{'role': 'user', 'text': 'a'}, {'role': 'model', 'text': 'b'}], 'draft': items[:1]})
    assert fake.calls[-1][1][1]['role'] == 'model' and fake.calls[-1][0]['rozpracovany_navrh'][0]['task_id'] == tid


def test_overnight_and_running_day(client, clock, fake):
    h = auth(client, EMP)
    clock.set('2026-11-05 01:30')
    punch(client, 'in', '2026-11-05T00:00', 'ai-000001')
    d = client.get(f'{A}/day?date=2026-11-05', headers=h).json()
    assert d['worked'] == [{'start': '00:00', 'end': '01:30', 'next_day': False, 'minutes': 90}]
    client.put('/api/journal/days/2026-11-03', headers=h, json={'segments': [{'start': '22:00', 'end': '02:00'}]})
    d = client.get(f'{A}/day?date=2026-11-03', headers=h).json()
    assert d['worked'][0]['next_day'] and d['worked_min'] == 240
    tid = client.post('/api/journal/tasks', headers=h, json={'name': 'noc'}).json()['id']
    r = client.post(f'{A}/apply', headers=h, json={'date': '2026-11-03', 'items': [{'start': '23:00', 'end': '01:00', 'task_id': tid, 'text': 'migrácia'}]}).json()
    assert r['created'] == 1
    a = client.get('/api/journal/activities', headers=h).json()[0]
    assert a['end_next_day'] and a['minutes'] == 120
    assert client.get(f'{A}/day?date=2026-11-03', headers=h).json()['free_min'] == 120


def test_gemini_error_is_readable(client, clock, monkeypatch):
    monkeypatch.setattr(gemini, 'configured', lambda: False)
    r = client.post(f'{A}/propose', headers=auth(client, EMP), json={'date': '2026-11-04', 'message': 'x'})
    assert r.status_code == 502 and 'GEMINI_API_KEY' in r.json()['detail']
    assert client.get(f'{A}/day?date=2026-11-04', headers=auth(client, EMP)).json()['configured'] is False

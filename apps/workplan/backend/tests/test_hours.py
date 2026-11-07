"""Výkaz hodín z dochádzky a žiadosti o absenciu na časť dňa."""
import io
from openpyxl import load_workbook
from tests.test_workplan import client, auth, plan  # noqa: F401  (fixtúra)


def add_doctor(client):
    r = client.post('/api/types', headers=auth(client), json={'name': 'Lekár', 'icon': 'Stethoscope', 'color': '#123456', 'requires_approval': True})
    assert r.json()['id'] == 4, r.text


def test_partial_leave_and_month_report(client, monkeypatch):
    from app import hours
    monkeypatch.setattr(hours, 'today', lambda: __import__('datetime').date(2026, 11, 30))
    add_doctor(client)
    emp, adm = auth(client, 2), auth(client)
    # počet hodín iba v jednom dni, po štvrťhodinách, menej ako úväzok
    assert client.post('/api/requests', headers=emp, json=plan(type_id=4, end_date='2026-11-03', hours=2)).status_code == 422
    assert client.post('/api/requests', headers=emp, json=plan(type_id=4, hours=1.1)).status_code == 422
    assert client.post('/api/requests', headers=emp, json=plan(type_id=4, hours=8)).status_code == 422
    assert client.post('/api/attendance', headers=emp, json=plan(start_date='2026-11-02', end_date='2026-11-06')).status_code == 200
    x = client.post('/api/requests', headers=emp, json=plan(type_id=4, hours=2, note='zubár')); assert x.status_code == 200, x.text; rid = x.json()['id']
    assert client.post(f'/api/requests/{rid}/review', headers=adm, json={'status': 'approved'}).status_code == 200
    cal = client.get('/api/calendar?month=2026-11', headers=emp).json()
    assert len(cal['entries']) == 5 and all(e['type_id'] == 1 for e in cal['entries'])   # plán ostal
    assert [p['hours'] for p in cal['partial']] == [2]
    # celodenná dovolenka 9.–10. 11.
    vid = client.post('/api/requests', headers=emp, json=plan(type_id=3, start_date='2026-11-09', end_date='2026-11-10')).json()['id']
    client.post(f'/api/requests/{vid}/review', headers=adm, json={'status': 'approved'})
    # na deň s dovolenkou sa časť dňa pridať nedá; čakajúca žiadosť sa do výkazu nepočíta
    assert client.post('/api/requests', headers=emp, json=plan(type_id=4, start_date='2026-11-09', end_date='2026-11-09', hours=1)).status_code == 409
    assert client.post('/api/requests', headers=emp, json=plan(type_id=4, start_date='2026-11-12', end_date='2026-11-12', hours=1)).status_code == 200
    client.put('/api/holidays', headers=adm, json={'date': '2026-11-17', 'name': 'Sviatok'})

    r = client.get('/api/hours?month=2026-11', headers=emp).json()
    assert [p['user_id'] for p in r['people']] == [2]          # zamestnanec vidí iba seba
    me = r['people'][0]
    # nov. 2026: 21 pracovných dní, 17. 11. sviatok → 20 × 8 h
    assert me['fund'] == 160 and me['holiday'] == 8
    assert me['absences'] == {'Lekár': 2, 'Vacation': 16} and me['absence_total'] == 18
    assert me['worked'] == 142 and me['difference'] == 0
    assert len(me['missing']) == 13                            # 13 pracovných dní bez plánu (vrátane 12. 11. – žiadosť čaká)
    assert next(d for d in me['days'] if d['date'] == '2026-11-02')['worked'] == 6

    # admin vidí všetkých, zamestnanec cudzí výkaz nie
    assert len(client.get('/api/hours?month=2026-11', headers=adm).json()['people']) == 3
    assert client.get('/api/hours?month=2026-11&user_id=3', headers=emp).status_code == 403
    # zrušenie schválenej časti dňa vráti hodiny
    client.delete(f'/api/requests/{rid}', headers=adm)
    assert client.get('/api/hours?month=2026-11', headers=emp).json()['people'][0]['worked'] == 144

    x = client.get('/api/hours/export?month=2026-11', headers=adm)
    assert x.status_code == 200
    wb = load_workbook(io.BytesIO(x.content))
    assert wb.sheetnames == ['Súhrn', 'Po dňoch'] and wb['Súhrn']['A1'].value == 'Zamestnanec'
    assert any(row[0] == 'User 2' and row[3] == 144 for row in wb['Súhrn'].iter_rows(values_only=True))


def test_partial_leave_conflicts_with_full_day_absence(client):
    add_doctor(client)
    emp, adm = auth(client, 2), auth(client)
    vid = client.post('/api/requests', headers=emp, json=plan(type_id=3)).json()['id']
    client.post(f'/api/requests/{vid}/review', headers=adm, json={'status': 'approved'})
    client.delete(f'/api/requests/{vid}', headers=adm)
    # admin zapíše PN priamo (bez žiadosti) – potom sa lekár na hodiny nedá
    sick = client.post('/api/types', headers=adm, json={'name': 'PN', 'icon': 'HeartPulse', 'color': '#123456', 'requires_approval': False})
    assert client.post('/api/attendance', headers=adm, json=plan(type_id=sick.json()['id'])).status_code == 200
    assert client.post('/api/requests', headers=emp, json=plan(type_id=4, hours=2)).status_code == 409

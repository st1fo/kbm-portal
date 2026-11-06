"""Nástenky úloh: osobná nástenka, tímové členstvo, presúvanie kariet a stĺpcov, Hotovo, checklist, oprávnenia."""
from test_workplan import client, auth  # noqa: F401 (fixture)
from test_journal_api import clock  # noqa: F401 (fixture)

ADMIN, EMP, OTHER = 1, 2, 3
B = '/api/journal'


def full(c, bid, i=EMP):
    return c.get(f'{B}/boards/{bid}', headers=auth(c, i)).json()


def titles(board, col_id):
    return [x['title'] for x in sorted((x for x in board['cards'] if x['column_id'] == col_id), key=lambda x: x['position'])]


def test_personal_board_cards_and_moves(client, clock):
    h = auth(client, EMP)
    boards = client.get(f'{B}/boards', headers=h).json()
    assert len(boards) == 1 and boards[0]['name'] == 'Moje úlohy' and not boards[0]['team']
    assert len(client.get(f'{B}/boards', headers=h).json()) == 1           # druhé otvorenie nezakladá ďalšiu
    bid = boards[0]['id']
    b = full(client, bid)
    todo, doing, done = [c['id'] for c in b['columns']]
    assert [c['is_done'] for c in b['columns']] == [False, False, True]
    pid = client.post(f'{B}/projects', headers=h, json={'name': 'Web'}).json()['id']
    a = client.post(f'{B}/boards/{bid}/cards', headers=h, json={'column_id': todo, 'title': 'A', 'due': '2026-11-04', 'priority': 2, 'customer_name': 'ACME'}).json()
    bb = client.post(f'{B}/boards/{bid}/cards', headers=h, json={'column_id': todo, 'title': 'B', 'project_id': pid}).json()
    cc = client.post(f'{B}/boards/{bid}/cards', headers=h, json={'column_id': todo, 'title': 'C', 'top': True}).json()
    assert a['customer'] == 'ACME' and bb['project'] == 'Web' and titles(full(client, bid), todo) == ['C', 'A', 'B']
    assert client.post(f'{B}/boards/{bid}/cards', headers=h, json={'column_id': todo, 'title': '  '}).status_code == 422
    assert client.post(f'{B}/boards/{bid}/cards', headers=h, json={'column_id': todo, 'title': 'x', 'priority': 3}).status_code == 422
    # presun v rámci stĺpca a do iného
    client.post(f"{B}/cards/{bb['id']}/move", headers=h, json={'index': 0})
    assert titles(full(client, bid), todo) == ['B', 'C', 'A']
    m = client.post(f"{B}/cards/{a['id']}/move", headers=h, json={'column_id': done, 'index': 0}).json()
    assert m['done_at'].startswith('2026-11-05T08:00') and m['column_id'] == done
    s = client.get(f'{B}/boards', headers=h).json()[0]
    assert s['open'] == 2 and s['late'] == 0                                # A je hotová, preto nie je po termíne
    m = client.post(f"{B}/cards/{a['id']}/move", headers=h, json={'column_id': doing, 'index': 5}).json()
    assert m['done_at'] is None and client.get(f'{B}/boards', headers=h).json()[0]['late'] == 1
    assert titles(full(client, bid), todo) == ['B', 'C'] and [x['position'] for x in full(client, bid)['cards'] if x['column_id'] == todo] == [0, 1]
    # úprava, checklist
    e = client.patch(f"{B}/cards/{a['id']}", headers=h, json={'title': 'A2', 'description': 'popis', 'due': None, 'project_id': pid}).json()
    assert (e['title'], e['description'], e['due'], e['project'], e['customer']) == ('A2', 'popis', None, 'Web', '')
    e = client.post(f"{B}/cards/{a['id']}/checklist", headers=h, json={'text': 'krok 1'}).json()
    e = client.post(f"{B}/cards/{a['id']}/checklist", headers=h, json={'text': 'krok 2'}).json()
    i1 = e['checklist'][0]['id']
    e = client.patch(f'{B}/checklist/{i1}', headers=h, json={'done': True}).json()
    assert [(x['text'], x['done']) for x in e['checklist']] == [('krok 1', True), ('krok 2', False)]
    assert [x['text'] for x in client.delete(f'{B}/checklist/{i1}', headers=h).json()['checklist']] == ['krok 2']
    # stĺpce: nový sa vloží pred Hotovo; Hotovo flag; mazanie neprázdneho nejde
    cols = client.post(f'{B}/boards/{bid}/columns', headers=h, json={'name': 'Čaká'}).json()['columns']
    assert [c['name'] for c in cols] == ['Na spracovanie', 'Robím', 'Čaká', 'Hotovo']
    cols = client.post(f"{B}/columns/{cols[2]['id']}/move", headers=h, json={'index': 0}).json()['columns']
    assert [c['name'] for c in cols] == ['Čaká', 'Na spracovanie', 'Robím', 'Hotovo']
    assert client.delete(f'{B}/columns/{todo}', headers=h).status_code == 409
    r = client.patch(f'{B}/columns/{doing}', headers=h, json={'is_done': True}).json()
    assert next(x for x in r['cards'] if x['id'] == a['id'])['done_at']
    assert client.post(f'{B}/columns/{doing}/clear', headers=h).json() == {'deleted': 1}
    assert client.delete(f'{B}/columns/{doing}', headers=h).status_code == 200
    # cudzí používateľ nič nevidí; admin áno a zápis ide do auditu
    assert client.get(f'{B}/boards/{bid}', headers=auth(client, OTHER)).status_code == 403
    assert client.patch(f"{B}/cards/{bb['id']}", headers=auth(client, OTHER), json={'title': 'x'}).status_code == 403
    assert client.get(f'{B}/boards?user_id={EMP}', headers=auth(client, OTHER)).status_code == 403
    assert [x['id'] for x in client.get(f'{B}/boards?user_id={EMP}', headers=auth(client)).json()] == [bid]
    assert client.patch(f"{B}/cards/{bb['id']}", headers=auth(client), json={'priority': 1}).status_code == 200
    assert 'journal.card.update' in [x['action'] for x in client.get('/api/audit', headers=auth(client)).json()]
    assert client.delete(f"{B}/cards/{bb['id']}", headers=h).status_code == 200
    # projekt použitý na karte sa nedá zmazať
    client.post(f'{B}/boards/{bid}/cards', headers=h, json={'column_id': todo, 'title': 'P', 'project_id': pid})
    assert client.delete(f'{B}/projects/{pid}', headers=auth(client)).status_code == 409


def test_team_board_members_and_assignees(client, clock):
    h, h3 = auth(client, EMP), auth(client, OTHER)
    t = client.post(f'{B}/boards', headers=h, json={'name': 'Tím web', 'color': '#2563eb', 'member_ids': [OTHER]}).json()
    assert t['team'] and t['member_ids'] == [EMP, OTHER] and t['can_manage'] and [m['name'] for m in t['members']] == ['User 2', 'User 3']
    assert client.post(f'{B}/boards', headers=h, json={'name': 'x', 'member_ids': [99]}).status_code == 422
    assert client.post(f'{B}/boards', headers=h, json={'name': 'x', 'color': 'red'}).status_code == 422
    col = t['columns'][0]['id']
    # člen vidí nástenku a môže pridávať karty, ale nie meniť nastavenia
    assert t['id'] in [x['id'] for x in client.get(f'{B}/boards', headers=h3).json()]
    assert not full(client, t['id'], OTHER)['can_manage']
    c = client.post(f"{B}/boards/{t['id']}/cards", headers=h3, json={'column_id': col, 'title': 'Spoločná', 'assignee_ids': [EMP, OTHER]}).json()
    assert c['assignee_ids'] == [EMP, OTHER] and c['created_by'] == OTHER
    assert client.patch(f"{B}/cards/{c['id']}", headers=h3, json={'assignee_ids': [ADMIN]}).status_code == 422   # admin nie je člen
    assert client.patch(f"{B}/boards/{t['id']}", headers=h3, json={'name': 'Prevzaté'}).status_code == 403
    assert client.delete(f"{B}/boards/{t['id']}", headers=h3).status_code == 403
    mine = client.get(f'{B}/my-cards', headers=h3).json()
    assert [(x['title'], x['board']) for x in mine] == [('Spoločná', 'Tím web')]
    assert next(x for x in client.get(f'{B}/boards', headers=h3).json() if x['id'] == t['id'])['mine'] == 1
    # odobratie člena zruší jeho priradenia
    client.patch(f"{B}/boards/{t['id']}", headers=h, json={'member_ids': []})
    assert client.get(f"{B}/boards/{t['id']}", headers=h3).status_code == 403
    assert full(client, t['id'])['cards'][0]['assignee_ids'] == [EMP]
    # opätovné pridanie, odchod člena, zmazanie zakladateľom
    client.patch(f"{B}/boards/{t['id']}", headers=h, json={'member_ids': [OTHER]})
    assert client.post(f"{B}/boards/{t['id']}/leave", headers=h3).status_code == 200
    assert client.post(f"{B}/boards/{t['id']}/leave", headers=h).status_code == 422
    assert client.delete(f"{B}/boards/{t['id']}", headers=h).status_code == 200
    assert client.get(f"{B}/boards/{t['id']}", headers=h).status_code == 404


def test_member_must_share_group(client, clock):
    from test_workplan import DIRECTORY, person
    DIRECTORY.append(person(4, groups=(2,)))
    client.get('/api/employees', headers=auth(client, ADMIN))   # adresár doplní lokálny účet u4
    r = client.post(f'{B}/boards', headers=auth(client, EMP), json={'name': 'x', 'member_ids': [4]})
    assert r.status_code == 403
    assert client.post(f'{B}/boards', headers=auth(client, ADMIN), json={'name': 'x', 'member_ids': [4]}).status_code == 200

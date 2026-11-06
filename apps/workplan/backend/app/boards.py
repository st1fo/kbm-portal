"""Nástenky úloh (kanban) v Denníku: stĺpce, karty s presúvaním, zodpovední ľudia, termín, priorita a checklist.

Oprávnenia: nástenku vidí a upravuje jej zakladateľ a vybraní členovia (bez členov je osobná). Názov, farbu,
členov a zmazanie mení iba zakladateľ. Admin môže otvoriť a upraviť každú nástenku; zmeny na cudzej nástenke
idú do auditu. Stĺpec označený „hotovo“ nastaví kartám čas dokončenia.
"""
import re
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, func, delete, update
from sqlalchemy.orm import Session
from .db import get_db
from .models import User, Board, BoardMember, BoardColumn, Card, CardAssignee, ChecklistItem
from .security import current_user
from .common import audit, commit
from .directory import visible_user_ids
from . import journal as J
from .journal import target_user, local, utc, catalog, check_project, resolve_customer

router = APIRouter(prefix='/api/journal', tags=['boards'])
DEFAULT_COLUMNS = [('Na spracovanie', False), ('Robím', False), ('Hotovo', True)]
COLOR = r'^#[0-9a-fA-F]{6}$'
MAX_COLUMNS, MAX_CARDS, MAX_CHECKLIST = 20, 2000, 100


def clean(v: str | None) -> str:
    return (v or '').replace('\x00', '').strip()


def required(v: str, what: str) -> str:
    v = ' '.join(clean(v).split())
    if not v: raise ValueError(f'Vyplňte {what}.')
    return v


# ---------------------------------------------------------------- prístup

def participants(db, b: Board) -> list[int]:
    return [b.owner_id] + [m for m in db.scalars(select(BoardMember.user_id).where(BoardMember.board_id == b.id).order_by(BoardMember.user_id)) if m != b.owner_id]


def get_board(db, u, bid: int) -> Board:
    b = db.get(Board, bid)
    if not b: raise HTTPException(404, 'Nástenka neexistuje.')
    if u.role != 'admin' and u.id not in participants(db, b): raise HTTPException(403, 'K tejto nástenke nemáte prístup.')
    return b


def note(db, u, b: Board, action: str, detail):
    """Admin upravuje nástenku, na ktorej nie je členom → audit."""
    if u.id not in participants(db, b): audit(db, u, action, {'board_id': b.id, **(detail if isinstance(detail, dict) else {'detail': detail})})


def owner_only(u, b: Board):
    if u.role != 'admin' and u.id != b.owner_id: raise HTTPException(403, 'Nastavenia nástenky mení iba jej zakladateľ.')


def check_members(db, u, ids: list[int]) -> set[int]:
    ids = set(ids)
    if not ids: return ids
    found = set(db.scalars(select(User.id).where(User.id.in_(ids), User.active == True)))  # noqa: E712
    if found != ids: raise HTTPException(422, 'Niektorý z vybraných ľudí neexistuje alebo nie je aktívny.')
    vis = visible_user_ids(db, u)
    if vis is not None and not ids <= vis: raise HTTPException(403, 'Pridať môžete iba ľudí zo svojho tímu.')
    return ids


def ensure_default_board(db, uid: int) -> Board:
    """Každý má aspoň jednu vlastnú nástenku; prvá vznikne pri prvom otvorení."""
    b = db.scalar(select(Board).where(Board.owner_id == uid).order_by(Board.id))
    if b: return b
    b = Board(name='Moje úlohy', owner_id=uid)
    db.add(b); db.flush()
    for i, (name, done) in enumerate(DEFAULT_COLUMNS): db.add(BoardColumn(board_id=b.id, name=name, position=i, is_done=done))
    db.flush()
    return b


# ---------------------------------------------------------------- výstupy

def names(db, ids) -> dict[int, str]:
    ids = set(ids)
    return {i: n for i, n in db.execute(select(User.id, User.name).where(User.id.in_(ids)))} if ids else {}


def board_summary(db, b: Board, uid: int, today: date) -> dict:
    people = participants(db, b)
    open_q = select(Card.id).join(BoardColumn, BoardColumn.id == Card.column_id).where(Card.board_id == b.id, BoardColumn.is_done == False)  # noqa: E712
    open_ids = select(open_q.subquery().c.id)
    count = lambda q: db.scalar(select(func.count()).select_from(q.subquery()))
    return {'id': b.id, 'name': b.name, 'color': b.color, 'owner_id': b.owner_id, 'member_ids': people, 'team': len(people) > 1,
            'open': count(open_q),
            'late': count(select(Card.id).where(Card.id.in_(open_ids), Card.due < today)),
            'mine': count(select(CardAssignee.card_id).where(CardAssignee.card_id.in_(open_ids), CardAssignee.user_id == uid))}


def card_out(c: Card, assignees: dict, checklist: dict, customers, projects) -> dict:
    p, cu = projects.get(c.project_id), customers.get(c.customer_id)
    items = checklist.get(c.id, [])
    return {'id': c.id, 'board_id': c.board_id, 'column_id': c.column_id, 'position': c.position, 'title': c.title,
            'description': c.description, 'due': c.due.isoformat() if c.due else None, 'priority': c.priority,
            'project_id': c.project_id, 'project': p.name if p else '',
            'project_customer': customers[p.customer_id].name if p and p.customer_id in customers else '',
            'customer_id': c.customer_id, 'customer': cu.name if cu else '',
            'assignee_ids': assignees.get(c.id, []),
            'checklist': [{'id': i.id, 'text': i.text, 'done': i.done} for i in items],
            'done_at': local(c.done_at).isoformat() if c.done_at else None, 'created_by': c.created_by,
            'created_at': local(c.created_at).isoformat()}


def cards_out(db, cards: list[Card]) -> list[dict]:
    ids = [c.id for c in cards]
    assignees, checklist = {}, {}
    if ids:
        for cid, uid in db.execute(select(CardAssignee.card_id, CardAssignee.user_id).where(CardAssignee.card_id.in_(ids)).order_by(CardAssignee.user_id)):
            assignees.setdefault(cid, []).append(uid)
        for i in db.scalars(select(ChecklistItem).where(ChecklistItem.card_id.in_(ids)).order_by(ChecklistItem.position, ChecklistItem.id)):
            checklist.setdefault(i.card_id, []).append(i)
    customers, projects, _ = catalog(db)
    return [card_out(c, assignees, checklist, customers, projects) for c in cards]


def board_full(db, b: Board, u) -> dict:
    cols = list(db.scalars(select(BoardColumn).where(BoardColumn.board_id == b.id).order_by(BoardColumn.position, BoardColumn.id)))
    cards = list(db.scalars(select(Card).where(Card.board_id == b.id).order_by(Card.position, Card.id)))
    people = participants(db, b)
    n = names(db, people)
    return {**board_summary(db, b, u.id, J.now().date()),
            'members': [{'id': i, 'name': n.get(i, '?')} for i in people],
            'can_manage': u.role == 'admin' or u.id == b.owner_id,
            'columns': [{'id': c.id, 'name': c.name, 'position': c.position, 'is_done': c.is_done} for c in cols],
            'cards': cards_out(db, cards)}


# ---------------------------------------------------------------- nástenky

class BoardIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = Field(default='#326c56', pattern=COLOR)
    member_ids: list[int] = Field(default_factory=list, max_length=100)

    @field_validator('name')
    @classmethod
    def v_name(cls, v): return required(v, 'názov nástenky')


class BoardPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    color: str | None = Field(default=None, pattern=COLOR)
    member_ids: list[int] | None = Field(default=None, max_length=100)

    @field_validator('name')
    @classmethod
    def v_name(cls, v): return required(v, 'názov nástenky') if v is not None else v


@router.get('/boards')
def boards(user_id: int | None = None, u=Depends(current_user), db: Session = Depends(get_db)):
    """Nástenky, ktorých je používateľ zakladateľom alebo členom (admin môže pozrieť cudzie cez user_id)."""
    uid = target_user(u, user_id, db)
    if uid == u.id and not db.scalar(select(Board.id).where(Board.owner_id == uid).limit(1)):
        ensure_default_board(db, uid); commit(db)
    member_of = select(BoardMember.board_id).where(BoardMember.user_id == uid)
    rows = db.scalars(select(Board).where((Board.owner_id == uid) | Board.id.in_(member_of)).order_by(Board.created_at, Board.id))
    today = J.now().date()
    return [board_summary(db, b, uid, today) for b in rows]


@router.post('/boards')
def add_board(p: BoardIn, u=Depends(current_user), db: Session = Depends(get_db)):
    members = check_members(db, u, p.member_ids) - {u.id}
    b = Board(name=p.name, color=p.color, owner_id=u.id)
    db.add(b); db.flush()
    for i, (name, done) in enumerate(DEFAULT_COLUMNS): db.add(BoardColumn(board_id=b.id, name=name, position=i, is_done=done))
    for m in members: db.add(BoardMember(board_id=b.id, user_id=m))
    commit(db)
    return board_full(db, b, u)


@router.get('/boards/{bid}')
def board(bid: int, u=Depends(current_user), db: Session = Depends(get_db)):
    return board_full(db, get_board(db, u, bid), u)


@router.patch('/boards/{bid}')
def edit_board(bid: int, p: BoardPatch, u=Depends(current_user), db: Session = Depends(get_db)):
    b = get_board(db, u, bid)
    owner_only(u, b)
    if p.name is not None: b.name = p.name
    if p.color is not None: b.color = p.color
    if p.member_ids is not None:
        old = set(participants(db, b)) - {b.owner_id}
        new = set(p.member_ids) - {b.owner_id}
        check_members(db, u, list(new - old))   # už prítomných nekontrolujeme (môžu byť z inej skupiny od admina)
        for m in old - new:
            db.execute(delete(BoardMember).where(BoardMember.board_id == b.id, BoardMember.user_id == m))
            db.execute(delete(CardAssignee).where(CardAssignee.user_id == m, CardAssignee.card_id.in_(select(Card.id).where(Card.board_id == b.id))))
        for m in new - old: db.add(BoardMember(board_id=b.id, user_id=m))
    note(db, u, b, 'journal.board.update', {'name': b.name})
    commit(db)
    return board_full(db, b, u)


def purge_board(db, bid: int):
    cards = select(Card.id).where(Card.board_id == bid)
    db.execute(delete(ChecklistItem).where(ChecklistItem.card_id.in_(cards)))
    db.execute(delete(CardAssignee).where(CardAssignee.card_id.in_(cards)))
    db.execute(delete(Card).where(Card.board_id == bid))
    db.execute(delete(BoardColumn).where(BoardColumn.board_id == bid))
    db.execute(delete(BoardMember).where(BoardMember.board_id == bid))
    db.execute(delete(Board).where(Board.id == bid))


@router.delete('/boards/{bid}')
def delete_board(bid: int, u=Depends(current_user), db: Session = Depends(get_db)):
    b = get_board(db, u, bid)
    owner_only(u, b)
    if u.id != b.owner_id: audit(db, u, 'journal.board.delete', {'board_id': b.id, 'name': b.name, 'owner_id': b.owner_id})
    purge_board(db, b.id)
    commit(db)
    return {'ok': True}


@router.post('/boards/{bid}/leave')
def leave_board(bid: int, u=Depends(current_user), db: Session = Depends(get_db)):
    b = get_board(db, u, bid)
    if b.owner_id == u.id: raise HTTPException(422, 'Zakladateľ nástenku neopúšťa; môže ju zmazať.')
    db.execute(delete(BoardMember).where(BoardMember.board_id == b.id, BoardMember.user_id == u.id))
    db.execute(delete(CardAssignee).where(CardAssignee.user_id == u.id, CardAssignee.card_id.in_(select(Card.id).where(Card.board_id == b.id))))
    commit(db)
    return {'ok': True}


# ---------------------------------------------------------------- stĺpce

class ColumnIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)

    @field_validator('name')
    @classmethod
    def v_name(cls, v): return required(v, 'názov stĺpca')


class ColumnPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    is_done: bool | None = None

    @field_validator('name')
    @classmethod
    def v_name(cls, v): return required(v, 'názov stĺpca') if v is not None else v


class MoveIn(BaseModel):
    index: int = Field(ge=0)
    column_id: int | None = None


def get_column(db, u, cid: int) -> tuple[BoardColumn, Board]:
    c = db.get(BoardColumn, cid)
    if not c: raise HTTPException(404, 'Stĺpec neexistuje.')
    return c, get_board(db, u, c.board_id)


def reorder(items, moving=None, index=None):
    """Prečísluje pozície 0..n; voliteľne vloží `moving` na `index`."""
    items = [x for x in items if x is not moving]
    if moving is not None: items.insert(min(index, len(items)), moving)
    for i, x in enumerate(items): x.position = i


def set_done(c: Card, done: bool):
    if done and c.done_at is None: c.done_at = utc(J.now())
    elif not done: c.done_at = None


@router.post('/boards/{bid}/columns')
def add_column(bid: int, p: ColumnIn, u=Depends(current_user), db: Session = Depends(get_db)):
    b = get_board(db, u, bid)
    n = db.scalar(select(func.count()).select_from(BoardColumn).where(BoardColumn.board_id == b.id))
    if n >= MAX_COLUMNS: raise HTTPException(422, f'Nástenka môže mať najviac {MAX_COLUMNS} stĺpcov.')
    # nový stĺpec pred prvý „hotový“, aby Hotovo ostalo na konci
    cols = list(db.scalars(select(BoardColumn).where(BoardColumn.board_id == b.id).order_by(BoardColumn.position, BoardColumn.id)))
    at = next((i for i, c in enumerate(cols) if c.is_done), len(cols))
    col = BoardColumn(board_id=b.id, name=p.name, position=0)
    db.add(col)
    reorder(cols, col, at)
    note(db, u, b, 'journal.column.create', {'name': p.name})
    commit(db)
    return board_full(db, b, u)


@router.patch('/columns/{cid}')
def edit_column(cid: int, p: ColumnPatch, u=Depends(current_user), db: Session = Depends(get_db)):
    col, b = get_column(db, u, cid)
    if p.name is not None: col.name = p.name
    if p.is_done is not None and p.is_done != col.is_done:
        col.is_done = p.is_done
        for c in db.scalars(select(Card).where(Card.column_id == col.id)): set_done(c, p.is_done)
    note(db, u, b, 'journal.column.update', {'id': cid})
    commit(db)
    return board_full(db, b, u)


@router.post('/columns/{cid}/move')
def move_column(cid: int, p: MoveIn, u=Depends(current_user), db: Session = Depends(get_db)):
    col, b = get_column(db, u, cid)
    reorder(list(db.scalars(select(BoardColumn).where(BoardColumn.board_id == b.id).order_by(BoardColumn.position, BoardColumn.id))), col, p.index)
    commit(db)
    return board_full(db, b, u)


@router.delete('/columns/{cid}')
def delete_column(cid: int, u=Depends(current_user), db: Session = Depends(get_db)):
    col, b = get_column(db, u, cid)
    if db.scalar(select(func.count()).select_from(BoardColumn).where(BoardColumn.board_id == b.id)) <= 1:
        raise HTTPException(422, 'Nástenka musí mať aspoň jeden stĺpec.')
    if db.scalar(select(func.count()).select_from(Card).where(Card.column_id == col.id)):
        raise HTTPException(409, 'V stĺpci sú karty. Presuňte ich alebo zmažte, potom stĺpec odstráňte.')
    db.delete(col); db.flush()
    reorder(list(db.scalars(select(BoardColumn).where(BoardColumn.board_id == b.id).order_by(BoardColumn.position, BoardColumn.id))))
    note(db, u, b, 'journal.column.delete', {'id': cid, 'name': col.name})
    commit(db)
    return board_full(db, b, u)


@router.post('/columns/{cid}/clear')
def clear_column(cid: int, u=Depends(current_user), db: Session = Depends(get_db)):
    """Zmaže všetky karty v stĺpci (typicky upratanie stĺpca Hotovo)."""
    col, b = get_column(db, u, cid)
    cards = select(Card.id).where(Card.column_id == col.id)
    db.execute(delete(ChecklistItem).where(ChecklistItem.card_id.in_(cards)))
    db.execute(delete(CardAssignee).where(CardAssignee.card_id.in_(cards)))
    n = db.execute(delete(Card).where(Card.column_id == col.id)).rowcount
    note(db, u, b, 'journal.column.clear', {'id': cid, 'deleted': n})
    commit(db)
    return {'deleted': n}


# ---------------------------------------------------------------- karty

class CardFields(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    due: date | None = None
    priority: int | None = Field(default=None, ge=0, le=2)
    project_id: int | None = None
    customer_id: int | None = None
    customer_name: str | None = Field(default=None, max_length=150)
    assignee_ids: list[int] | None = Field(default=None, max_length=50)

    @field_validator('title')
    @classmethod
    def v_title(cls, v): return required(v, 'názov karty') if v is not None else v


class CardIn(CardFields):
    column_id: int
    title: str = Field(min_length=1, max_length=500)
    top: bool = False


def get_card(db, u, cid: int) -> tuple[Card, Board]:
    c = db.get(Card, cid)
    if not c: raise HTTPException(404, 'Karta neexistuje.')
    return c, get_board(db, u, c.board_id)


def apply_fields(db, u, b: Board, c: Card, p: CardFields):
    fields = p.model_fields_set
    if p.title is not None: c.title = p.title
    if p.description is not None: c.description = clean(p.description)
    if 'due' in fields: c.due = p.due
    if p.priority is not None: c.priority = p.priority
    if p.project_id is not None:
        c.project_id, c.customer_id = check_project(db, p.project_id), None
    elif fields & {'project_id', 'customer_id', 'customer_name'}:
        c.project_id, c.customer_id = None, resolve_customer(db, u, p.customer_id, p.customer_name)
    if p.assignee_ids is not None:
        want = set(p.assignee_ids)
        if not want <= set(participants(db, b)): raise HTTPException(422, 'Zodpovedný človek musí byť členom nástenky.')
        db.flush()
        have = set(db.scalars(select(CardAssignee.user_id).where(CardAssignee.card_id == c.id)))
        if have - want: db.execute(delete(CardAssignee).where(CardAssignee.card_id == c.id, CardAssignee.user_id.in_(have - want)))
        for m in want - have: db.add(CardAssignee(card_id=c.id, user_id=m))


def one_card(db, c: Card) -> dict:
    db.flush()
    return cards_out(db, [c])[0]


@router.post('/boards/{bid}/cards')
def add_card(bid: int, p: CardIn, u=Depends(current_user), db: Session = Depends(get_db)):
    b = get_board(db, u, bid)
    col = db.get(BoardColumn, p.column_id)
    if not col or col.board_id != b.id: raise HTTPException(422, 'Stĺpec nepatrí k tejto nástenke.')
    if db.scalar(select(func.count()).select_from(Card).where(Card.board_id == b.id)) >= MAX_CARDS:
        raise HTTPException(422, f'Nástenka môže mať najviac {MAX_CARDS} kariet. Upracte hotové karty.')
    siblings = list(db.scalars(select(Card).where(Card.column_id == col.id).order_by(Card.position, Card.id)))
    c = Card(board_id=b.id, column_id=col.id, position=0, title=p.title, description='', priority=0, created_by=u.id)
    db.add(c); db.flush()
    apply_fields(db, u, b, c, p)
    set_done(c, col.is_done)
    reorder(siblings, c, 0 if p.top else len(siblings))
    note(db, u, b, 'journal.card.create', {'title': c.title})
    commit(db)
    return one_card(db, c)


@router.patch('/cards/{cid}')
def edit_card(cid: int, p: CardFields, u=Depends(current_user), db: Session = Depends(get_db)):
    c, b = get_card(db, u, cid)
    apply_fields(db, u, b, c, p)
    note(db, u, b, 'journal.card.update', {'id': cid})
    commit(db)
    return one_card(db, c)


@router.post('/cards/{cid}/move')
def move_card(cid: int, p: MoveIn, u=Depends(current_user), db: Session = Depends(get_db)):
    """Presun karty (ťahaním) na pozíciu `index` v stĺpci `column_id` (predvolene v tom istom)."""
    c, b = get_card(db, u, cid)
    target = db.get(BoardColumn, p.column_id) if p.column_id is not None else db.get(BoardColumn, c.column_id)
    if not target or target.board_id != b.id: raise HTTPException(422, 'Stĺpec nepatrí k tejto nástenke.')
    old = c.column_id
    if old != target.id:
        reorder([x for x in db.scalars(select(Card).where(Card.column_id == old).order_by(Card.position, Card.id)) if x is not c])
        c.column_id = target.id
        set_done(c, target.is_done)
    reorder(list(db.scalars(select(Card).where(Card.column_id == target.id, Card.id != c.id).order_by(Card.position, Card.id))), c, p.index)
    note(db, u, b, 'journal.card.move', {'id': cid, 'column': target.name})
    commit(db)
    return one_card(db, c)


@router.delete('/cards/{cid}')
def delete_card(cid: int, u=Depends(current_user), db: Session = Depends(get_db)):
    c, b = get_card(db, u, cid)
    col = c.column_id
    db.execute(delete(ChecklistItem).where(ChecklistItem.card_id == c.id))
    db.execute(delete(CardAssignee).where(CardAssignee.card_id == c.id))
    db.delete(c); db.flush()
    reorder(list(db.scalars(select(Card).where(Card.column_id == col).order_by(Card.position, Card.id))))
    note(db, u, b, 'journal.card.delete', {'id': cid, 'title': c.title})
    commit(db)
    return {'ok': True}


# ---------------------------------------------------------------- checklist

class ItemIn(BaseModel):
    text: str = Field(min_length=1, max_length=300)

    @field_validator('text')
    @classmethod
    def v_text(cls, v): return required(v, 'text položky')


class ItemPatch(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=300)
    done: bool | None = None

    @field_validator('text')
    @classmethod
    def v_text(cls, v): return required(v, 'text položky') if v is not None else v


@router.post('/cards/{cid}/checklist')
def add_item(cid: int, p: ItemIn, u=Depends(current_user), db: Session = Depends(get_db)):
    c, b = get_card(db, u, cid)
    n = db.scalar(select(func.count()).select_from(ChecklistItem).where(ChecklistItem.card_id == c.id))
    if n >= MAX_CHECKLIST: raise HTTPException(422, f'Checklist môže mať najviac {MAX_CHECKLIST} položiek.')
    db.add(ChecklistItem(card_id=c.id, text=p.text, position=n))
    note(db, u, b, 'journal.card.update', {'id': cid})
    commit(db)
    return one_card(db, c)


def get_item(db, u, iid: int) -> tuple[ChecklistItem, Card, Board]:
    i = db.get(ChecklistItem, iid)
    if not i: raise HTTPException(404, 'Položka neexistuje.')
    return (i, *get_card(db, u, i.card_id))


@router.patch('/checklist/{iid}')
def edit_item(iid: int, p: ItemPatch, u=Depends(current_user), db: Session = Depends(get_db)):
    i, c, b = get_item(db, u, iid)
    if p.text is not None: i.text = p.text
    if p.done is not None: i.done = p.done
    note(db, u, b, 'journal.card.update', {'id': c.id})
    commit(db)
    return one_card(db, c)


@router.delete('/checklist/{iid}')
def delete_item(iid: int, u=Depends(current_user), db: Session = Depends(get_db)):
    i, c, b = get_item(db, u, iid)
    db.delete(i); db.flush()
    reorder(list(db.scalars(select(ChecklistItem).where(ChecklistItem.card_id == c.id).order_by(ChecklistItem.position, ChecklistItem.id))))
    note(db, u, b, 'journal.card.update', {'id': c.id})
    commit(db)
    return one_card(db, c)


# ---------------------------------------------------------------- moje karty naprieč nástenkami

@router.get('/my-cards')
def my_cards(u=Depends(current_user), db: Session = Depends(get_db)):
    """Otvorené karty priradené mne zo všetkých nástenok (pre prehľad „Moje úlohy“)."""
    mine = select(CardAssignee.card_id).where(CardAssignee.user_id == u.id)
    q = (select(Card).join(BoardColumn, BoardColumn.id == Card.column_id)
         .where(Card.id.in_(mine), BoardColumn.is_done == False)  # noqa: E712
         .order_by(Card.due.is_(None), Card.due, Card.priority.desc(), Card.id))
    cards = list(db.scalars(q))
    boards_ = {b.id: b for b in db.scalars(select(Board).where(Board.id.in_({c.board_id for c in cards})))} if cards else {}
    return [{**x, 'board': boards_[x['board_id']].name} for x in cards_out(db, cards)]

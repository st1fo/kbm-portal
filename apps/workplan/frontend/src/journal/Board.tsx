import {
  DndContext, DragOverlay, KeyboardSensor, MouseSensor, TouchSensor, closestCenter, closestCorners, pointerWithin, useSensor, useSensors,
} from '@dnd-kit/core';
import type { CollisionDetection, DragEndEvent, DragOverEvent, DragStartEvent } from '@dnd-kit/core';
import { SortableContext, arrayMove, horizontalListSortingStrategy, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { AlignLeft, CheckCircle2, CheckSquare, GripVertical, LogOut, MoreHorizontal, Plus, Search, Settings, Users, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { User } from '../api';
import type { BoardColumn, BoardFull, Card } from './api';
import { journalApi } from './api';
import type { Prefill } from './Activities';
import { BoardDialog, CardDialog } from './BoardDialogs';
import { addDays, baNow, plural, weekday } from './calc';
import { useBoard, useBoards, useLocalSetting, useRun } from './hooks';
import { Avatar, dueInfo } from './boardUtil';
import './board.css';

type Filter = 'all' | 'mine' | 'week' | 'late';
const FILTERS: [Filter, string][] = [['all', 'Všetky'], ['mine', 'Moje'], ['week', 'Tento týždeň'], ['late', 'Po termíne']];
const cardKey = (id: number) => 'k:' + id, colKey = (id: number) => 'col:' + id;
const idOf = (k: string | number) => Number(String(k).split(':')[1]);
const isCol = (k: string | number) => String(k).startsWith('col:');

/** Pri ťahaní stĺpca hľadá iba stĺpce; pri karte uprednostní kartu pod prstom/myšou, inak najbližší stĺpec. */
const collision: CollisionDetection = args => {
  if (isCol(args.active.id)) return closestCenter({ ...args, droppableContainers: args.droppableContainers.filter(c => isCol(c.id)) });
  const hits = pointerWithin(args);
  const cards = hits.filter(h => !isCol(h.id));
  if (cards.length) return cards;
  if (hits.length) return hits;
  return closestCorners(args);
};

/** Nástenky úloh (kanban) v Denníku – náhrada pôvodného to-do. */
export function BoardsPage({ uid, me, users, onWrite, onError }: { uid: number | null; me: User; users: User[]; onWrite: (p: Omit<Prefill, 'nonce'>) => void; onError: (m: string) => void }) {
  const run = useRun(onError);
  const boards = useBoards(uid);
  const list = boards.data ?? [];
  const [stored, setStored] = useLocalSetting('kbm-board-' + (uid ?? 'me'), '');
  const [creating, setCreating] = useState(false);
  const current = list.find(b => String(b.id) === stored) ?? list[0];
  const personal = list.filter(b => !b.team), team = list.filter(b => b.team);
  const chip = (b: typeof list[number]) => <button key={b.id} className={'kb-chip' + (current?.id === b.id ? ' on' : '')} onClick={() => setStored(String(b.id))} aria-pressed={current?.id === b.id}>
    <i style={{ background: b.color }} />{b.team && <Users size={14} aria-label="Tímová" />}<span>{b.name}</span>
    {b.open > 0 && <b className={b.late ? 'late' : ''} title={`${b.open} otvorených${b.late ? `, ${b.late} po termíne` : ''}`}>{b.open}</b>}
  </button>;
  return <div className="kb">
    <div className="kb-boards" role="toolbar" aria-label="Nástenky">
      {personal.length > 0 && <span className="kb-group">Moje</span>}{personal.map(chip)}
      {team.length > 0 && <span className="kb-group">Tímové</span>}{team.map(chip)}
      {!uid && <button className="kb-chip add" onClick={() => setCreating(true)}><Plus size={15} />Nová nástenka</button>}
    </div>
    {boards.isError && <div className="error" role="alert">Nástenky sa nepodarilo načítať. <button onClick={() => void boards.refetch()}>Skúsiť znova</button></div>}
    {boards.isLoading && <p className="kb-empty">Načítavam nástenky…</p>}
    {current && <BoardView key={current.id} id={current.id} uid={uid} me={me} users={users} onWrite={onWrite} onError={onError} onGone={() => setStored('')} />}
    {creating && <BoardDialog me={me} users={users} onClose={() => setCreating(false)} onSave={async data => {
      const b = await run(() => journalApi.addBoard(data));
      if (b) { setStored(String(b.id)); setCreating(false); }
    }} />}
  </div>;
}

function BoardView({ id, uid, me, users, onWrite, onError, onGone }: { id: number; uid: number | null; me: User; users: User[]; onWrite: (p: Omit<Prefill, 'nonce'>) => void; onError: (m: string) => void; onGone: () => void }) {
  const run = useRun(onError);
  const [dragging, setDragging] = useState<string | null>(null);
  const [teamPoll, setTeamPoll] = useState(false);
  const q = useBoard(id, teamPoll && !dragging ? 15000 : false);
  const b = q.data;
  useEffect(() => setTeamPoll(!!b?.team), [b?.team]);
  const [filter, setFilter] = useLocalSetting('kbm-board-filter', 'all') as [Filter, (v: string) => void];
  const [search, setSearch] = useState('');
  const [openCard, setOpenCard] = useState<number | null>(null);
  const [settings, setSettings] = useState(false);
  const [addingCol, setAddingCol] = useState(false);
  const [order, setOrder] = useState<Record<number, number[]> | null>(null);   // dočasné poradie počas ťahania/ukladania
  const [colOrder, setColOrder] = useState<number[] | null>(null);
  const startCol = useRef<number | null>(null);
  const today = baNow().date;
  const who = uid ?? me.id;

  const sensors = useSensors(
    useSensor(MouseSensor, { activationConstraint: { distance: 5 } }),
    useSensor(TouchSensor, { activationConstraint: { delay: 220, tolerance: 8 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );
  const cards = useMemo(() => new Map((b?.cards ?? []).map(c => [c.id, c])), [b]);
  const base = useMemo(() => {
    const m: Record<number, number[]> = {};
    for (const c of b?.columns ?? []) m[c.id] = [];
    for (const c of [...(b?.cards ?? [])].sort((x, y) => x.position - y.position)) (m[c.column_id] ??= []).push(c.id);
    return m;
  }, [b]);
  if (q.isError) return <div className="error" role="alert">Nástenka nie je dostupná (možno bola zmazaná alebo vás z nej odobrali). <button onClick={onGone}>Späť na moje nástenky</button></div>;
  if (!b) return <p className="kb-empty">Načítavam nástenku…</p>;

  const items = order ?? base;
  const columns = (colOrder ?? b.columns.map(c => c.id)).map(cid => b.columns.find(c => c.id === cid)!).filter(Boolean);
  const needle = search.trim().toLowerCase();
  const filtering = filter !== 'all' || !!needle;
  const sunday = addDays(today, 6 - weekday(today));
  const visible = (c: Card) => {
    if (needle && !(c.title + ' ' + c.description + ' ' + c.project + ' ' + c.customer).toLowerCase().includes(needle)) return false;
    if (filter === 'mine') return c.assignee_ids.includes(who);
    if (filter === 'week') return !!c.due && c.due <= sunday && !c.done_at;
    if (filter === 'late') return !!c.due && c.due < today && !c.done_at;
    return true;
  };
  const containerOf = (key: string | number) => isCol(key) ? idOf(key) : Number(Object.keys(items).find(k => items[Number(k)].includes(idOf(key))));
  const peopleOf = (ids: number[]) => ids.map(i => b.members.find(m => m.id === i) ?? { id: i, name: '?' });

  function onDragStart(e: DragStartEvent) {
    setDragging(String(e.active.id));
    if (!isCol(e.active.id)) { setOrder(structuredClone(base)); startCol.current = containerOf(e.active.id); }
  }
  function onDragOver(e: DragOverEvent) {
    const { active, over } = e;
    if (!over || isCol(active.id) || !order) return;
    const from = containerOf(active.id), to = containerOf(over.id);
    if (Number.isNaN(from) || Number.isNaN(to) || from === to) return;
    setOrder(o => {
      if (!o) return o;
      const id = idOf(active.id);
      const src = o[from].filter(x => x !== id), dst = [...o[to]];
      const at = isCol(over.id) ? dst.length : Math.max(0, dst.indexOf(idOf(over.id)));
      dst.splice(at, 0, id);
      return { ...o, [from]: src, [to]: dst };
    });
  }
  async function onDragEnd(e: DragEndEvent) {
    const { active, over } = e;
    setDragging(null);
    if (isCol(active.id)) {
      const ids = b!.columns.map(c => c.id);
      const from = ids.indexOf(idOf(active.id)), to = over ? ids.indexOf(idOf(over.id)) : -1;
      if (to < 0 || from === to) return;
      setColOrder(arrayMove(ids, from, to));
      await run(() => journalApi.moveColumn(idOf(active.id), to));
      setColOrder(null);
      return;
    }
    if (!order || !over) { setOrder(null); return; }
    const id = idOf(active.id), col = containerOf(active.id);
    let list = order[col];
    if (!isCol(over.id) && containerOf(over.id) === col) {
      const a = list.indexOf(id), z = list.indexOf(idOf(over.id));
      if (a !== z && z >= 0) list = arrayMove(list, a, z);
    }
    const next = { ...order, [col]: list };
    const index = list.indexOf(id);
    if (col === startCol.current && base[col].indexOf(id) === index) { setOrder(null); return; }
    setOrder(next);
    await run(() => journalApi.moveCard(id, col, index));
    setOrder(null);
  }

  const active = dragging && !isCol(dragging) ? cards.get(idOf(dragging)) : null;
  const activeCol = dragging && isCol(dragging) ? b.columns.find(c => c.id === idOf(dragging)) : null;
  const total = b.cards.filter(c => !c.done_at).length;
  return <section className="kb-board" style={{ '--kb-accent': b.color } as React.CSSProperties}>
    <header className="kb-head">
      <div className="kb-title">
        <h2>{b.name}</h2>
        <span className="kb-sub">{total} {plural(total, 'otvorená karta', 'otvorené karty', 'otvorených kariet')}{b.team ? ` · tím ${b.members.length} ${plural(b.members.length, 'človek', 'ľudia', 'ľudí')}` : ' · osobná'}</span>
      </div>
      {b.team && <div className="kb-people" aria-label="Členovia">{b.members.slice(0, 6).map(m => <Avatar key={m.id} id={m.id} name={m.name} />)}{b.members.length > 6 && <span className="kb-more">+{b.members.length - 6}</span>}</div>}
      <div className="kb-tools">
        <label className="kb-search"><Search size={15} /><input value={search} placeholder="Hľadať karty" aria-label="Hľadať karty" onChange={e => setSearch(e.target.value)} />{search && <button className="icon-button" aria-label="Zrušiť hľadanie" onClick={() => setSearch('')}><X size={14} /></button>}</label>
        <div className="kb-filter" role="group" aria-label="Filter kariet">{FILTERS.filter(([k]) => k !== 'mine' || b.team).map(([k, l]) => <button key={k} aria-pressed={filter === k} className={filter === k ? 'on' : ''} onClick={() => setFilter(k)}>{l}</button>)}</div>
        {b.can_manage
          ? <button className="secondary compact" onClick={() => setSettings(true)} aria-label="Nastavenia nástenky"><Settings size={15} /><span className="kb-hide-sm">Nastavenia</span></button>
          : b.team && !uid && <button className="secondary compact" onClick={() => { if (window.confirm(`Opustiť nástenku „${b.name}“? Zrušia sa aj vaše priradenia na kartách.`)) void run(() => journalApi.leaveBoard(b.id)).then(r => r && onGone()); }}><LogOut size={15} /><span className="kb-hide-sm">Opustiť</span></button>}
      </div>
    </header>
    {filtering && <p className="kb-hint">Zobrazené sú iba vyhovujúce karty. Pri filtri sa karty nedajú preťahovať, presunúť ich môžeš v detaile karty.</p>}
    <DndContext sensors={sensors} collisionDetection={collision} onDragStart={onDragStart} onDragOver={onDragOver} onDragEnd={e => void onDragEnd(e)} onDragCancel={() => { setDragging(null); setOrder(null); }}>
      <div className="kb-cols">
        <SortableContext items={columns.map(c => colKey(c.id))} strategy={horizontalListSortingStrategy}>
          {columns.map(col => <Column key={col.id} col={col} board={b} ids={items[col.id] ?? []} cards={cards} visible={visible} filtering={filtering} today={today}
            peopleOf={peopleOf} onOpen={setOpenCard} onError={onError} />)}
        </SortableContext>
        <div className="kb-col kb-addcol">
          {addingCol
            ? <QuickInput placeholder="Názov stĺpca" maxLength={60} onCancel={() => setAddingCol(false)} onSubmit={async name => { if (await run(() => journalApi.addColumn(b.id, name))) setAddingCol(false); }} />
            : <button className="kb-addcol-btn" onClick={() => setAddingCol(true)}><Plus size={16} />Pridať stĺpec</button>}
        </div>
      </div>
      <DragOverlay dropAnimation={null}>
        {active && <CardFace card={active} today={today} people={peopleOf(active.assignee_ids)} overlay />}
        {activeCol && <div className="kb-col overlay"><div className="kb-col-head"><h3>{activeCol.name}</h3></div></div>}
      </DragOverlay>
    </DndContext>
    {openCard != null && cards.get(openCard) && <CardDialog card={cards.get(openCard)!} board={b} uid={uid} onWrite={p => { setOpenCard(null); onWrite(p); }} onClose={() => setOpenCard(null)} onError={onError} />}
    {settings && <BoardDialog me={me} users={users} board={b} onClose={() => setSettings(false)}
      onSave={async data => { if (await run(() => journalApi.editBoard(b.id, data))) setSettings(false); }}
      onDelete={async () => { if (await run(() => journalApi.deleteBoard(b.id))) { setSettings(false); onGone(); } }} />}
  </section>;
}

function Column({ col, board, ids, cards, visible, filtering, today, peopleOf, onOpen, onError }: {
  col: BoardColumn; board: BoardFull; ids: number[]; cards: Map<number, Card>; visible: (c: Card) => boolean; filtering: boolean; today: string;
  peopleOf: (ids: number[]) => { id: number; name: string }[]; onOpen: (id: number) => void; onError: (m: string) => void;
}) {
  const run = useRun(onError);
  const { setNodeRef, setActivatorNodeRef, attributes, listeners, transform, transition, isDragging } = useSortable({ id: colKey(col.id), disabled: filtering });
  const [adding, setAdding] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [menu, setMenu] = useState(false);
  const shown = ids.map(i => cards.get(i)).filter((c): c is Card => !!c && visible(c));
  const count = ids.length;
  return <div ref={setNodeRef} className={'kb-col' + (col.is_done ? ' done' : '') + (isDragging ? ' ghost' : '')} style={{ transform: CSS.Translate.toString(transform), transition }} aria-label={'Stĺpec ' + col.name}>
    <div className="kb-col-head">
      {!filtering && <button ref={setActivatorNodeRef} className="kb-grip" aria-label={'Presunúť stĺpec ' + col.name} {...attributes} {...listeners}><GripVertical size={15} /></button>}
      {renaming
        ? <QuickInput initial={col.name} maxLength={60} onCancel={() => setRenaming(false)} onSubmit={async name => { if (await run(() => journalApi.editColumn(col.id, { name }))) setRenaming(false); }} />
        : <h3 onDoubleClick={() => setRenaming(true)} title="Dvojklik premenuje">{col.is_done && <CheckCircle2 size={15} aria-label="Stĺpec hotových" />}{col.name}</h3>}
      <span className="kb-count">{filtering && shown.length !== count ? `${shown.length}/${count}` : count}</span>
      <div className="kb-menu-wrap">
        <button className="icon-button" aria-label={'Možnosti stĺpca ' + col.name} aria-expanded={menu} onClick={() => setMenu(!menu)}><MoreHorizontal size={17} /></button>
        {menu && <Menu onClose={() => setMenu(false)} items={[
          ['Premenovať', () => setRenaming(true)],
          [col.is_done ? 'Nepočítať ako hotové' : 'Karty tu sú hotové', () => void run(() => journalApi.editColumn(col.id, { is_done: !col.is_done }))],
          ...(count ? [[`Zmazať všetky karty (${count})`, () => { if (window.confirm(`Natrvalo zmazať ${count} ${plural(count, 'kartu', 'karty', 'kariet')} zo stĺpca „${col.name}“?`)) void run(() => journalApi.clearColumn(col.id)); }, true] as MenuItem] : []),
          ['Odstrániť stĺpec', () => {
            if (count) { onError('V stĺpci sú karty. Presuň ich alebo zmaž, potom stĺpec odstráň.'); return; }
            if (board.columns.length <= 1) { onError('Nástenka musí mať aspoň jeden stĺpec.'); return; }
            if (window.confirm(`Odstrániť prázdny stĺpec „${col.name}“?`)) void run(() => journalApi.deleteColumn(col.id));
          }, true],
        ]} />}
      </div>
    </div>
    <SortableContext items={ids.map(cardKey)} strategy={verticalListSortingStrategy}>
      <div className="kb-cards">
        {shown.map(c => <SortableCard key={c.id} card={c} today={today} people={peopleOf(c.assignee_ids)} disabled={filtering} onOpen={() => onOpen(c.id)} />)}
        {!shown.length && <p className="kb-col-empty">{filtering ? 'Nič nevyhovuje filtru' : col.is_done ? 'Sem presuň hotové karty' : 'Žiadne karty'}</p>}
      </div>
    </SortableContext>
    {adding
      ? <QuickInput multiline placeholder="Čo treba spraviť? (Enter pridá, Esc zavrie)" maxLength={500} keepOpen onCancel={() => setAdding(false)}
        onSubmit={async title => !!(await run(() => journalApi.addCard(board.id, { column_id: col.id, title })))} />
      : <button className="kb-add" onClick={() => setAdding(true)}><Plus size={16} />Pridať kartu</button>}
  </div>;
}

function SortableCard({ card, today, people, disabled, onOpen }: { card: Card; today: string; people: { id: number; name: string }[]; disabled: boolean; onOpen: () => void }) {
  const { setNodeRef, attributes, listeners, transform, transition, isDragging } = useSortable({ id: cardKey(card.id), disabled });
  return <div ref={setNodeRef} style={{ transform: CSS.Translate.toString(transform), transition }} className={isDragging ? 'kb-ghost' : ''} {...attributes} {...listeners}
    onClick={onOpen} onKeyDown={e => { if (e.key === 'Enter') onOpen(); listeners?.onKeyDown?.(e); }} aria-roledescription="karta" aria-label={card.title}>
    <CardFace card={card} today={today} people={people} />
  </div>;
}

export function CardFace({ card, today, people, overlay }: { card: Card; today: string; people: { id: number; name: string }[]; overlay?: boolean }) {
  const di = !card.done_at ? dueInfo(card.due, today) : null;
  const done = card.checklist.filter(i => i.done).length;
  return <article className={'kb-card p' + card.priority + (card.done_at ? ' done' : '') + (overlay ? ' overlay' : '')}>
    <p className="kb-card-title">{card.done_at && <CheckCircle2 size={15} className="kb-done-ico" />}{card.title}</p>
    <div className="kb-card-meta">
      {di && <span className={'j-chip ' + di.cls}>{di.label}</span>}
      {card.priority > 0 && !card.done_at && <span className={'kb-prio p' + card.priority}>{card.priority === 2 ? 'Vysoká' : 'Stredná'}</span>}
      {card.project ? <span className="j-chip proj" title={card.project + (card.project_customer ? ' — ' + card.project_customer : '')}>{card.project}</span> : card.customer && <span className="j-chip cust">{card.customer}</span>}
      {card.checklist.length > 0 && <span className={'kb-badge' + (done === card.checklist.length ? ' ok' : '')} title="Checklist"><CheckSquare size={13} />{done}/{card.checklist.length}</span>}
      {card.description && <span className="kb-badge" title="Má popis"><AlignLeft size={13} /></span>}
      {people.length > 0 && <span className="kb-assignees">{people.map(p => <Avatar key={p.id} id={p.id} name={p.name} small />)}</span>}
    </div>
  </article>;
}

type MenuItem = [string, () => void, boolean?];
function Menu({ items, onClose }: { items: MenuItem[]; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const out = (e: PointerEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) onClose(); };
    const esc = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('pointerdown', out); document.addEventListener('keydown', esc);
    return () => { document.removeEventListener('pointerdown', out); document.removeEventListener('keydown', esc); };
  }, [onClose]);
  return <div className="kb-menu" role="menu" ref={ref}>{items.map(([label, fn, danger]) => <button key={label} role="menuitem" className={danger ? 'danger-text' : ''} onClick={() => { onClose(); fn(); }}>{label}</button>)}</div>;
}

/** Malé pole na rýchle pridanie/premenovanie. Pri `keepOpen` ostane otvorené na ďalšiu kartu. */
function QuickInput({ initial = '', placeholder, maxLength, multiline, keepOpen, onSubmit, onCancel }: {
  initial?: string; placeholder?: string; maxLength: number; multiline?: boolean; keepOpen?: boolean; onSubmit: (v: string) => Promise<unknown>; onCancel: () => void;
}) {
  const [v, setV] = useState(initial);
  const [busy, setBusy] = useState(false);
  async function submit() {
    const t = v.trim();
    if (!t) { if (!keepOpen) onCancel(); return; }
    if (t === initial) { onCancel(); return; }
    setBusy(true);
    const ok = await onSubmit(t);
    setBusy(false);
    if (ok !== false && keepOpen) setV('');
  }
  const keys = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void submit(); }
    else if (e.key === 'Escape') onCancel();
  };
  return <div className="kb-quick">
    {multiline
      ? <textarea autoFocus rows={2} value={v} placeholder={placeholder} maxLength={maxLength} aria-label={placeholder} onChange={e => setV(e.target.value)} onKeyDown={keys} />
      : <input autoFocus value={v} placeholder={placeholder} maxLength={maxLength} aria-label={placeholder ?? 'Názov'} onChange={e => setV(e.target.value)} onKeyDown={keys} onBlur={() => !keepOpen && void submit()} />}
    {(multiline || keepOpen) && <div className="kb-quick-acts"><button className="primary compact" disabled={busy || !v.trim()} onClick={() => void submit()}>Pridať</button><button className="icon-button" aria-label="Zavrieť" onClick={onCancel}><X size={16} /></button></div>}
  </div>;
}

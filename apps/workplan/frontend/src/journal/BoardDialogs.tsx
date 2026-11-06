import { Check, NotebookPen, Plus, Trash2, X } from 'lucide-react';
import { useState } from 'react';
import { Dialog } from '../components/ui/dialog';
import type { User } from '../api';
import type { Assign, BoardFull, Card, Customer, Project, Task } from './api';
import { journalApi } from './api';
import type { Prefill } from './Activities';
import { baNow } from './calc';
import { useCustomers, useOptimisticFlags, useProjects, useRun, useTasks } from './hooks';
import { Avatar } from './boardUtil';

const PRIO = ['Bežná', 'Stredná', 'Vysoká'];
export const COLORS = ['#326c56', '#2563eb', '#7c3aed', '#db2777', '#d97706', '#0891b2', '#4b5563'];

const assignOf = (t: { project_id: number | null; customer_id: number | null }) => t.project_id ? 'p:' + t.project_id : t.customer_id ? 'c:' + t.customer_id : '';
function assignPayload(v: string): Assign {
  if (v.startsWith('p:')) return { project_id: Number(v.slice(2)) };
  if (v.startsWith('c:')) return { customer_id: Number(v.slice(2)) };
  if (v.startsWith('n:')) return { customer_name: v.slice(2) };
  return { project_id: null, customer_id: null };
}

/** Jeden select „projekt alebo zákazník“ s možnosťou nového zákazníka. */
function AssignSelect({ value, onChange, projects, customers }: { value: string; onChange: (v: string) => void; projects: Project[]; customers: Customer[] }) {
  return <select aria-label="Projekt alebo zákazník" value={value} onChange={e => {
    if (e.target.value !== 'new') { onChange(e.target.value); return; }
    const n = window.prompt('Názov nového zákazníka:', '')?.trim();
    if (n) onChange('n:' + n);
  }}>
    <option value="">Bez projektu / zákazníka</option>
    {projects.length > 0 && <optgroup label="Projekt">{projects.slice().sort((a, b) => a.name.localeCompare(b.name, 'sk')).map(p => <option key={p.id} value={'p:' + p.id}>{p.name}{p.customer ? ' — ' + p.customer : ''}</option>)}</optgroup>}
    <optgroup label="Zákazník (bez projektu)">
      {customers.map(c => <option key={c.id} value={'c:' + c.id}>{c.name}</option>)}
      {value.startsWith('n:') && <option value={value}>{value.slice(2)} (nový)</option>}
      <option value="new">+ nový zákazník…</option>
    </optgroup>
  </select>;
}

/** Detail karty: všetky polia, presun do stĺpca, checklist, zapísanie ako aktivity. */
export function CardDialog({ card, board, uid, onClose, onWrite, onError }: { card: Card; board: BoardFull; uid: number | null; onClose: () => void; onWrite: (p: Omit<Prefill, 'nonce'>) => void; onError: (m: string) => void }) {
  const run = useRun(onError);
  const projects = useProjects().data ?? [];
  const customers = useCustomers().data ?? [];
  const tasks = useTasks(uid).data ?? [];
  const [title, setTitle] = useState(card.title), [desc, setDesc] = useState(card.description);
  const [due, setDue] = useState(card.due ?? ''), [prio, setPrio] = useState(card.priority), [assign, setAssign] = useState(assignOf(card));
  const [people, setPeople] = useState<number[]>(card.assignee_ids), [column, setColumn] = useState(card.column_id);
  const [item, setItem] = useState('');
  const flags = useOptimisticFlags();
  const [busy, setBusy] = useState(false);
  const dirty = title !== card.title || desc !== card.description || due !== (card.due ?? '') || prio !== card.priority || assign !== assignOf(card)
    || people.join() !== card.assignee_ids.join() || column !== card.column_id;

  async function save(close = true) {
    if (!title.trim()) { onError('Karta musí mať názov.'); return false; }
    setBusy(true);
    let ok = true;
    if (column !== card.column_id) ok = !!await run(() => journalApi.moveCard(card.id, column, 0));
    if (ok) ok = !!await run(() => journalApi.editCard(card.id, { title, description: desc, due: due || null, priority: prio, assignee_ids: people, ...assignPayload(assign) }));
    setBusy(false);
    if (ok && close) onClose();
    return ok;
  }
  async function addItem() {
    const t = item.trim();
    if (t && await run(() => journalApi.addItem(card.id, t))) setItem('');
  }
  function toActivity() {
    const cands: Task[] = tasks.filter(x => x.active && !x.done && (card.project_id ? x.project_id === card.project_id : !x.project_id && (!card.customer_id || x.customer_id === card.customer_id)));
    const pick = cands.find(x => x.name.toLowerCase() === card.title.toLowerCase()) ?? (cands.length === 1 ? cands[0] : undefined);
    const base = { projectId: card.project_id ?? '' as const, date: card.done_at?.slice(0, 10) ?? baNow().date, text: card.title };
    if (pick) onWrite({ ...base, taskId: pick.id, hint: 'Predvyplnené z karty. Doplň čas od–do a daj Zapísať (Ctrl+Enter).' });
    else if (!cands.length) onWrite({ ...base, taskId: '', newTask: card.title, newTaskCustomer: card.project_id ? '' : card.customer, warn: true, hint: 'Predvyplnené z karty. Najprv klikni „Pridať úlohu“ (je predvyplnená), potom doplň čas a Zapísať.' });
    else onWrite({ ...base, taskId: '', warn: true, hint: 'Predvyplnené z karty — vyber úlohu zo zoznamu, potom doplň čas a Zapísať.' });
  }
  const close = () => { if (!dirty || window.confirm('Zahodiť neuložené zmeny na karte?')) onClose(); };
  const doneCount = card.checklist.filter(i => flags.get(i.id, i.done)).length;
  return <Dialog title="Karta" onClose={close}>
    <div className="kb-dialog">
      <label>Názov<textarea className="kb-title-input" rows={2} value={title} maxLength={500} onChange={e => setTitle(e.target.value)} /></label>
      <div className="form-grid">
        <label>Stĺpec<select value={column} onChange={e => setColumn(Number(e.target.value))}>{board.columns.map(c => <option key={c.id} value={c.id}>{c.name}{c.is_done ? ' ✓' : ''}</option>)}</select></label>
        <label>Priorita<select value={prio} onChange={e => setPrio(Number(e.target.value))}>{PRIO.map((l, i) => <option key={i} value={i}>{l}</option>)}</select></label>
        <label>Termín<input type="date" value={due} onChange={e => setDue(e.target.value)} /></label>
        <label>Projekt / zákazník<AssignSelect value={assign} onChange={setAssign} projects={projects} customers={customers} /></label>
      </div>
      {board.members.length > 1 && <fieldset className="kb-field"><legend>Zodpovední</legend>
        <div className="kb-people-pick">{board.members.map(m => {
          const on = people.includes(m.id);
          return <button key={m.id} type="button" aria-pressed={on} className={on ? 'on' : ''} onClick={() => setPeople(on ? people.filter(x => x !== m.id) : [...people, m.id].sort((a, b) => a - b))}>
            <Avatar id={m.id} name={m.name} small />{m.name}{on && <Check size={14} />}</button>;
        })}</div></fieldset>}
      <label>Popis<textarea rows={4} value={desc} maxLength={5000} placeholder="Podrobnosti, odkazy, poznámky…" onChange={e => setDesc(e.target.value)} /></label>
      <fieldset className="kb-field"><legend>Checklist {card.checklist.length > 0 && <span className="kb-sub">{doneCount}/{card.checklist.length}</span>}</legend>
        {card.checklist.length > 0 && <div className="kb-progress" aria-hidden><i style={{ width: `${Math.round(100 * doneCount / card.checklist.length)}%` }} /></div>}
        {card.checklist.map(i => <div key={i.id} className={'kb-item' + (flags.get(i.id, i.done) ? ' done' : '')}>
          <input type="checkbox" className="j-check" checked={flags.get(i.id, i.done)} aria-label={'Hotové: ' + i.text} onChange={e => { const v = e.target.checked; void flags.toggle(i.id, v, () => run(() => journalApi.editItem(i.id, { done: v }))); }} />
          <span>{i.text}</span>
          <button className="icon-button" aria-label={'Zmazať položku ' + i.text} onClick={() => void run(() => journalApi.deleteItem(i.id))}><X size={14} /></button>
        </div>)}
        <div className="kb-item-add"><input value={item} maxLength={300} placeholder="Nová položka (Enter)" aria-label="Nová položka checklistu" onChange={e => setItem(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); void addItem(); } }} />
          <button className="secondary compact" disabled={!item.trim()} onClick={() => void addItem()}><Plus size={15} />Pridať</button></div>
      </fieldset>
      <div className="dialog-actions kb-dialog-actions">
        <button className="danger compact" onClick={() => { if (window.confirm(`Natrvalo zmazať kartu „${card.title}“?`)) void run(() => journalApi.deleteCard(card.id)).then(r => r && onClose()); }}><Trash2 size={15} />Zmazať</button>
        <button className="secondary compact" title="Predvyplní formulár „Zapísať, čo robím“ v časti Dnes" onClick={async () => { if (!dirty || await save(false)) toActivity(); }}><NotebookPen size={15} />Zapísať ako aktivitu</button>
        <span className="kb-spacer" />
        <button className="secondary compact" onClick={close}>Zavrieť</button>
        <button className="primary compact" disabled={busy || !dirty} onClick={() => void save()}>Uložiť</button>
      </div>
    </div>
  </Dialog>;
}

/** Nová nástenka alebo nastavenia existujúcej (názov, farba, členovia, zmazanie). */
export function BoardDialog({ me, users, board, onSave, onDelete, onClose }: {
  me: User; users: User[]; board?: BoardFull; onSave: (d: { name: string; color: string; member_ids: number[] }) => Promise<void>; onDelete?: () => Promise<void>; onClose: () => void;
}) {
  const owner = board?.owner_id ?? me.id;
  const [name, setName] = useState(board?.name ?? '');
  const [color, setColor] = useState(board?.color ?? COLORS[0]);
  const [members, setMembers] = useState<number[]>(board ? board.member_ids.filter(i => i !== owner) : []);
  const [teamMode, setTeamMode] = useState(!!board?.team);
  const [busy, setBusy] = useState(false);
  const known = new Map(users.filter(u => u.active).map(u => [u.id, u.name]));
  for (const m of board?.members ?? []) if (!known.has(m.id)) known.set(m.id, m.name);
  const candidates = [...known].filter(([id]) => id !== owner).sort((a, b) => a[1].localeCompare(b[1], 'sk'));
  async function submit() {
    if (!name.trim()) return;
    setBusy(true);
    await onSave({ name: name.trim(), color, member_ids: teamMode ? members : [] });
    setBusy(false);
  }
  return <Dialog title={board ? 'Nastavenia nástenky' : 'Nová nástenka'} onClose={onClose}>
    <label>Názov<input autoFocus value={name} maxLength={100} placeholder="napr. Web pre ACME, Moje úlohy" onChange={e => setName(e.target.value)} onKeyDown={e => e.key === 'Enter' && void submit()} /></label>
    <fieldset className="kb-field"><legend>Farba</legend>
      <div className="kb-colors">{COLORS.map(c => <button key={c} type="button" aria-label={'Farba ' + c} aria-pressed={color === c} className={color === c ? 'on' : ''} style={{ background: c }} onClick={() => setColor(c)} />)}</div>
    </fieldset>
    <fieldset className="kb-field"><legend>Kto ju uvidí</legend>
      <div className="kb-seg" role="radiogroup">
        <button type="button" role="radio" aria-checked={!teamMode} className={!teamMode ? 'on' : ''} onClick={() => setTeamMode(false)}><strong>Osobná</strong><small>iba ja</small></button>
        <button type="button" role="radio" aria-checked={teamMode} className={teamMode ? 'on' : ''} onClick={() => setTeamMode(true)}><strong>Tímová</strong><small>ja a vybraní ľudia</small></button>
      </div>
      {teamMode && <>
        <p className="kb-sub">Členovia môžu pridávať, upravovať a presúvať karty aj stĺpce. Názov, farbu, členov a zmazanie mení iba zakladateľ.</p>
        <div className="kb-people-pick">{candidates.length ? candidates.map(([id, n]) => {
          const on = members.includes(id);
          return <button key={id} type="button" aria-pressed={on} className={on ? 'on' : ''} onClick={() => setMembers(on ? members.filter(x => x !== id) : [...members, id])}>
            <Avatar id={id} name={n} small />{n}{on && <Check size={14} />}</button>;
        }) : <span className="kb-sub">Nevidíš nikoho ďalšieho. Ľudí do tímu pridáva správca v portáli (skupiny).</span>}</div>
      </>}
    </fieldset>
    <div className="dialog-actions">
      {board && onDelete && <button className="danger compact" onClick={() => {
        const n = board.cards.length;
        if (window.confirm(`Natrvalo zmazať nástenku „${board.name}“${n ? ` aj s ${n} kartami` : ''}? ${board.team ? 'Zmizne všetkým členom.' : ''}`)) void onDelete();
      }}><Trash2 size={15} />Zmazať nástenku</button>}
      <span className="kb-spacer" />
      <button className="secondary compact" onClick={onClose}>Zrušiť</button>
      <button className="primary compact" disabled={busy || !name.trim() || (teamMode && !members.length)} title={teamMode && !members.length ? 'Vyber aspoň jedného člena' : undefined} onClick={() => void submit()}>{board ? 'Uložiť' : 'Vytvoriť'}</button>
    </div>
  </Dialog>;
}

import { Pencil, X } from 'lucide-react';
import { useState } from 'react';
import type { Assign, Customer, Project, Task, Todo } from './api';
import { journalApi } from './api';
import { DAY_NAMES, addDays, baNow, daysBetween, plural, weekday } from './calc';
import type { Prefill } from './Activities';
import { useCustomers, useLocalSetting, useOptimisticFlags, useProjects, useRun, useTasks, useTodos } from './hooks';

const PRIO = ['Bežná', 'Stredná', 'Vysoká'];
type Tab = 'today' | 'week' | 'all' | 'late';
const TABS: [Tab, string, string][] = [['today', 'Dnes', 'Termín dnes alebo skôr'], ['week', 'Týždeň', 'Termín do nedele (vrátane po termíne)'], ['all', 'Všetko', 'Všetky otvorené'], ['late', 'Po termíne', 'Termín už prešiel']];
const EMPTY: Record<Tab, string> = { today: 'Na dnes nič s termínom.', week: 'Tento týždeň nič s termínom.', late: 'Nič nie je po termíne.', all: 'Žiadne to-do. Napíš hore, čo treba spraviť.' };

const doneDay = (t: Todo) => t.done_at?.slice(0, 10) ?? null;   // done_at je miestny čas s posunom
function inTab(t: Todo, tab: Tab, today: string) {
  if (tab === 'today') return !!t.due && t.due <= today;
  if (tab === 'week') return !!t.due && t.due <= addDays(today, 6 - weekday(today));
  if (tab === 'late') return !!t.due && t.due < today;
  return true;
}
function dueInfo(t: Todo, today: string) {
  if (!t.due) return null;
  const diff = daysBetween(today, t.due);
  let label = diff === 0 ? 'dnes' : diff === 1 ? 'zajtra' : diff === -1 ? 'včera' : `${DAY_NAMES[weekday(t.due)].toLowerCase()} ${Number(t.due.slice(8))}.${Number(t.due.slice(5, 7))}.`;
  if (diff < -1) label = 'od ' + label;
  return { label, cls: diff < 0 ? 'late' : diff === 0 ? 'today' : 'due' };
}
const assignOf = (t: { project_id: number | null; customer_id: number | null }) => t.project_id ? 'p:' + t.project_id : t.customer_id ? 'c:' + t.customer_id : '';
function assignPayload(v: string): Assign {
  if (v.startsWith('p:')) return { project_id: Number(v.slice(2)) };
  if (v.startsWith('c:')) return { customer_id: Number(v.slice(2)) };
  if (v.startsWith('n:')) return { customer_name: v.slice(2) };
  return { project_id: null, customer_id: null };
}

/** Jeden select „projekt alebo zákazník“ s možnosťou nového zákazníka (fillAssignSelect). */
function AssignSelect({ value, onChange, projects, customers, className }: { value: string; onChange: (v: string) => void; projects: Project[]; customers: Customer[]; className?: string }) {
  return <select className={className} aria-label="Projekt alebo zákazník" value={value} onChange={e => {
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

function TodoEditor({ t, projects, customers, onDone, onError }: { t: Todo; projects: Project[]; customers: Customer[]; onDone: () => void; onError: (m: string) => void }) {
  const run = useRun(onError);
  const [text, setText] = useState(t.text), [assign, setAssign] = useState(assignOf(t)), [due, setDue] = useState(t.due ?? ''), [prio, setPrio] = useState(t.priority);
  async function save() {
    if (!text.trim()) return;
    if (await run(() => journalApi.editTodo(t.id, { text, due: due || null, priority: prio, ...assignPayload(assign) }))) onDone();
  }
  const keys = (e: { key: string; preventDefault: () => void }) => { if (e.key === 'Enter') { e.preventDefault(); void save(); } else if (e.key === 'Escape') onDone(); };
  return <div className="j-td-edit">
    <input autoFocus value={text} maxLength={500} aria-label="Text to-do" onChange={e => setText(e.target.value)} onKeyDown={keys} />
    <div className="j-row">
      <AssignSelect value={assign} onChange={setAssign} projects={projects} customers={customers} className="j-grow" />
      <input type="date" value={due} aria-label="Termín" onChange={e => setDue(e.target.value)} onKeyDown={keys} />
      <select value={prio} aria-label="Priorita" onChange={e => setPrio(Number(e.target.value))}>{PRIO.map((l, i) => <option key={i} value={i}>{l}</option>)}</select>
    </div>
    <div className="j-acts"><button className="secondary compact" onClick={onDone}>Zrušiť</button><button className="primary compact" onClick={() => void save()}>Uložiť</button></div>
  </div>;
}

/** To-do s termínmi, prioritou a prevodom na aktivitu (renderTodos, todoToActivity). */
export function TodoCard({ uid, onWrite, onError }: { uid: number | null; onWrite: (p: Omit<Prefill, 'nonce'>) => void; onError: (m: string) => void }) {
  const run = useRun(onError);
  const todos = useTodos(uid).data ?? [];
  const projects = useProjects().data ?? [];
  const customers = useCustomers().data ?? [];
  const tasks = useTasks(uid).data ?? [];
  const [tab, setTab] = useLocalSetting('kbm-journal-todo-tab', 'all') as [Tab, (v: string) => void];
  const [text, setText] = useState(''), [assign, setAssign] = useState(''), [due, setDue] = useState(''), [prio, setPrio] = useState(0);
  const [editing, setEditing] = useState<number | null>(null);
  const [showOld, setShowOld] = useState(false);
  const flags = useOptimisticFlags();
  const today = baNow().date;
  const open = todos.filter(t => !t.done);
  const doneToday = todos.filter(t => t.done && doneDay(t) === today).sort((a, b) => (b.done_at ?? '').localeCompare(a.done_at ?? ''));
  const doneOld = todos.filter(t => t.done && doneDay(t) !== today).sort((a, b) => (b.done_at ?? '').localeCompare(a.done_at ?? ''));
  const late = open.filter(t => inTab(t, 'late', today)).length;
  const sorted = open.filter(t => inTab(t, tab, today)).sort((a, b) => (a.due ?? '9999').localeCompare(b.due ?? '9999') || b.priority - a.priority || a.created_at.localeCompare(b.created_at));
  const list = [...sorted, ...(tab === 'all' || tab === 'today' ? doneToday : [])];

  async function add() {
    if (!text.trim()) return;
    const t = await run(() => journalApi.addTodo({ text, due: due || null, priority: prio, ...assignPayload(assign) }, uid));
    if (!t) return;
    if (!inTab(t, tab, today)) setTab('all');
    setText(''); setDue(''); setPrio(0); setAssign('');
  }
  function toActivity(t: Todo) {
    const cands: Task[] = tasks.filter(x => x.active && !x.done && (t.project_id ? x.project_id === t.project_id : !x.project_id && (!t.customer_id || x.customer_id === t.customer_id)));
    const pick = cands.find(x => x.name.toLowerCase() === t.text.toLowerCase()) ?? (cands.length === 1 ? cands[0] : undefined);
    const base = { projectId: t.project_id ?? '' as const, date: doneDay(t) ?? today, text: t.text };
    if (pick) onWrite({ ...base, taskId: pick.id, hint: 'Predvyplnené z to-do. Doplň čas od–do a daj Zapísať (Ctrl+Enter).' });
    else if (!cands.length) onWrite({ ...base, taskId: '', newTask: t.text, newTaskCustomer: t.project_id ? '' : t.customer, warn: true, hint: 'Predvyplnené z to-do. Najprv klikni „Pridať úlohu“ (je predvyplnená), potom doplň čas a Zapísať.' });
    else onWrite({ ...base, taskId: '', warn: true, hint: 'Predvyplnené z to-do — vyber úlohu zo zoznamu, potom doplň čas a Zapísať.' });
  }
  const row = (t: Todo) => {
    if (editing === t.id) return <TodoEditor key={t.id} t={t} projects={projects} customers={customers} onDone={() => setEditing(null)} onError={onError} />;
    const di = dueInfo(t, today);
    return <div className={'j-td' + (t.done ? ' done' : '')} key={t.id}>
      <span className={'j-prio p' + t.priority} title={'Priorita: ' + PRIO[t.priority]} />
      <input type="checkbox" className="j-check" checked={flags.get(t.id, t.done)} aria-label={'Hotové: ' + t.text} onChange={e => { const v = e.target.checked; void flags.toggle(t.id, v, () => run(() => journalApi.editTodo(t.id, { done: v }))); }} />
      <span className="j-td-text" title="Klikni pre úpravu" onClick={() => !t.done && setEditing(t.id)}>{t.text}</span>
      {t.done
        ? <button className="j-to-act" title="Predvyplní formulár „Zapísať, čo robím“" onClick={() => toActivity(t)}>→ zapísať ako aktivitu</button>
        : <span className="j-chips">
          {di && <span className={'j-chip ' + di.cls}>{di.label}</span>}
          {t.project ? <span className="j-chip proj" title={t.project + (t.project_customer ? ' — ' + t.project_customer : '')}>{t.project}</span> : t.customer && <span className="j-chip cust">{t.customer}</span>}
        </span>}
      {!t.done && <button className="icon-button" aria-label="Upraviť to-do" onClick={() => setEditing(t.id)}><Pencil size={14} /></button>}
      <button className="icon-button" aria-label="Zmazať to-do" onClick={() => void run(() => journalApi.deleteTodo(t.id))}><X size={15} /></button>
    </div>;
  };
  const allDone = todos.filter(t => t.done).length;
  return <section className="j-card">
    <div className="j-card-head"><h2>To-do</h2>
      <span className={'j-todo-count' + (late ? ' late' : '')}>{open.length ? `${open.length} ${plural(open.length, 'otvorené', 'otvorené', 'otvorených')}${late ? ` · ${late} po termíne` : ''}` : ''}</span></div>
    <div className="j-row">
      <input className="j-grow" value={text} maxLength={500} placeholder="Nové to-do… (Enter)" aria-label="Nové to-do" onChange={e => setText(e.target.value)} onKeyDown={e => e.key === 'Enter' && void add()} />
      <button className="primary compact" onClick={() => void add()}>Pridať</button>
    </div>
    <div className="j-row j-todo-opts">
      <AssignSelect value={assign} onChange={setAssign} projects={projects} customers={customers} className="j-grow" />
      <input type="date" value={due} aria-label="Termín" title="Termín (nepovinné)" onChange={e => setDue(e.target.value)} />
      <select value={prio} aria-label="Priorita" onChange={e => setPrio(Number(e.target.value))}>{PRIO.map((l, i) => <option key={i} value={i}>{l}</option>)}</select>
    </div>
    <div className="j-tabs" role="tablist">{TABS.map(([k, label, title]) => {
      const n = open.filter(t => inTab(t, k, today)).length;
      return <button key={k} role="tab" aria-selected={tab === k} title={title} className={'j-tab' + (tab === k ? ' on' : '') + (k === 'late' && n ? ' late' : '')} onClick={() => setTab(k)}>{label}<b>{n}</b></button>;
    })}</div>
    <div>{list.length ? list.map(row) : <p className="j-empty-small">{EMPTY[tab]}</p>}</div>
    <div className="j-todo-foot">
      {doneOld.length > 0 && <button className="text-button" onClick={() => setShowOld(!showOld)}>{showOld ? '▾' : '▸'} Staršie hotové ({doneOld.length})</button>}
      {allDone > 0 && <button className="text-button" onClick={() => { if (window.confirm(`Vymazať ${allDone} ${plural(allDone, 'hotové to-do', 'hotové to-do', 'hotových to-do')}?`)) void run(() => journalApi.clearDoneTodos(uid)); }}>Vymazať hotové</button>}
      {showOld && <div className="j-full">{doneOld.map(row)}</div>}
    </div>
  </section>;
}

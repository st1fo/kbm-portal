import { Pencil, Plus, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { KeyboardEvent } from 'react';
import type { Activity, Project, Task } from './api';
import { journalApi } from './api';
import { DAY_NAMES, addDays, fmtDur, prettyDate, spanPreview, weekday } from './calc';
import { useCustomers, useProjects, useRecentActivities, useRun, useTasks } from './hooks';

export type Prefill = { nonce: number; projectId: number | ''; taskId: number | ''; date?: string; text?: string; newTask?: string; newTaskCustomer?: string; hint?: string; warn?: boolean };

const dayShort = (k: string) => `${DAY_NAMES[weekday(k)]} ${Number(k.slice(8))}.${Number(k.slice(5, 7))}.`;
const taskLabel = (t: Task) => t.name + (!t.project_id && t.customer ? ' — ' + t.customer : '');
const byName = <T extends { name: string }>(a: T, b: T) => a.name.localeCompare(b.name, 'sk');

function SpanText({ date, from, to }: { date: string; from: string; to: string }) {
  if (!from || !to) return null;
  const s = spanPreview(from, to);
  return s.next
    ? <><em className="j-nd">+1 deň</em>{dayShort(date)} {from} → {dayShort(addDays(date, 1))} {to} · {fmtDur(s.minutes)}</>
    : <>{from} → {to} · {fmtDur(s.minutes)}</>;
}

/** „Zapísať, čo robím“: projekt, úloha (s pridaním), kedy, text; Ctrl+Enter zapíše (saveActivity). */
export function ActivityForm({ uid, defaultDate, prefill, onError }: { uid: number | null; defaultDate: string; prefill: Prefill | null; onError: (m: string) => void }) {
  const run = useRun(onError);
  const projects = (useProjects().data ?? []).slice().sort(byName);
  const tasks = useTasks(uid).data ?? [];
  const customers = useCustomers().data ?? [];
  const [projectSel, setProjectSel] = useState<number | ''>('');
  const [taskSel, setTaskSel] = useState<number | ''>('');
  const [addProject, setAddProject] = useState(false);
  const [addTask, setAddTask] = useState(false);
  const [np, setNp] = useState({ name: '', customer: '' });
  const [nt, setNt] = useState({ name: '', customer: '' });
  const [date, setDate] = useState(defaultDate);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [text, setText] = useState('');
  const [hint, setHint] = useState<{ text: string; warn: boolean } | null>(null);
  const textRef = useRef<HTMLTextAreaElement>(null);
  const card = useRef<HTMLElement>(null);

  useEffect(() => {
    if (!prefill) return;
    setProjectSel(prefill.projectId); setTaskSel(prefill.taskId);
    if (prefill.date) setDate(prefill.date);
    if (prefill.text !== undefined) setText(prefill.text);
    if (prefill.newTask !== undefined) { setNt({ name: prefill.newTask, customer: prefill.newTaskCustomer ?? '' }); setAddTask(true); }
    if (prefill.hint) setHint({ text: prefill.hint, warn: !!prefill.warn });
    card.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    card.current?.classList.remove('j-flash'); void card.current?.offsetWidth; card.current?.classList.add('j-flash');
    setTimeout(() => textRef.current?.focus({ preventScroll: true }), 350);
  }, [prefill?.nonce]); // eslint-disable-line react-hooks/exhaustive-deps

  const project = projects.find(p => p.id === projectSel);
  const options = tasks.filter(t => t.active && !t.done && (projectSel ? t.project_id === projectSel : !t.project_id)).sort(byName);
  useEffect(() => { if (taskSel && tasks.length && !options.some(t => t.id === taskSel)) setTaskSel(''); }, [projectSel, tasks.length]); // eslint-disable-line react-hooks/exhaustive-deps

  async function saveProject() {
    if (!np.name.trim()) return;
    const p = await run(() => journalApi.addProject(np.name, np.customer));
    if (p) { setProjectSel(p.id); setTaskSel(''); setNp({ name: '', customer: '' }); setAddProject(false); }
  }
  async function saveTask() {
    if (!nt.name.trim()) return;
    const t = await run(() => journalApi.addTask({ name: nt.name, project_id: projectSel || null, customer_name: projectSel ? null : nt.customer || null }));
    if (t) { setTaskSel(t.id); setNt({ name: '', customer: '' }); setAddTask(false); textRef.current?.focus(); }
  }
  async function save() {
    if (!text.trim()) { textRef.current?.focus(); return; }
    if (!taskSel) { setHint({ text: 'Najprv vyber úlohu (alebo ju pridaj cez +).', warn: true }); return; }
    if (to && !from) { setHint({ text: 'Zadaj aj čas „od“.', warn: true }); return; }
    const a = await run(() => journalApi.addActivity({ task_id: Number(taskSel), date: date || defaultDate, start: from || null, end: to || null, text }, uid));
    if (!a) return;
    setText(''); setFrom(''); setTo('');
    setHint({ text: a.minutes != null ? `Zapísané ✓ (${a.minutes} min)` : 'Zapísané ✓', warn: false });
    textRef.current?.focus();
  }
  return <section className="j-card" ref={card}>
    <h2>Zapísať, čo robím</h2>
    <div className="j-stack">
      <div>
        <label className="j-lbl" htmlFor="j-project">Projekt</label>
        <div className="j-pick">
          <select id="j-project" value={projectSel} onChange={e => { setProjectSel(e.target.value ? Number(e.target.value) : ''); setTaskSel(''); }}>
            <option value="">(bez projektu)</option>
            {projects.map(p => <option key={p.id} value={p.id}>{p.name}{p.customer ? ' — ' + p.customer : ''}</option>)}
          </select>
          <button className="j-mini" aria-label="Nový projekt" title="Nový projekt" onClick={() => setAddProject(!addProject)}><Plus size={17} /></button>
        </div>
      </div>
      {addProject && <div className="j-inline-add">
        <label>Názov projektu<input autoFocus value={np.name} maxLength={150} placeholder="napr. Infraštruktúra" onChange={e => setNp({ ...np, name: e.target.value })} onKeyDown={e => e.key === 'Enter' && void saveProject()} /></label>
        <label>Zákazník<input value={np.customer} list="j-customers" maxLength={150} placeholder="napr. Žilinská univerzita" onChange={e => setNp({ ...np, customer: e.target.value })} onKeyDown={e => e.key === 'Enter' && void saveProject()} /></label>
        <button className="primary" onClick={() => void saveProject()}>Pridať projekt</button>
      </div>}
      <div>
        <label className="j-lbl" htmlFor="j-task">Úloha</label>
        <div className="j-pick">
          <select id="j-task" value={taskSel} onChange={e => setTaskSel(e.target.value ? Number(e.target.value) : '')}>
            <option value="">{options.length ? '— vyber úlohu —' : '— žiadna úloha, pridaj cez +'}</option>
            {options.map(t => <option key={t.id} value={t.id}>{taskLabel(t)}</option>)}
          </select>
          <button className="j-mini" aria-label="Nová úloha" title="Nová úloha" onClick={() => setAddTask(!addTask)}><Plus size={17} /></button>
        </div>
      </div>
      {addTask && <div className="j-inline-add">
        <label>Názov úlohy<input autoFocus value={nt.name} maxLength={200} placeholder="napr. Migrácia VM" onChange={e => setNt({ ...nt, name: e.target.value })} onKeyDown={e => e.key === 'Enter' && void saveTask()} /></label>
        {!projectSel && <label>Zákazník <span className="j-opt">(nepovinné)</span><input value={nt.customer} list="j-customers" maxLength={150} onChange={e => setNt({ ...nt, customer: e.target.value })} onKeyDown={e => e.key === 'Enter' && void saveTask()} /></label>}
        <button className="primary" onClick={() => void saveTask()}>Pridať úlohu</button>
        <p className="j-note">{project ? `Úloha sa pridá do projektu „${project.name}“${project.customer ? ` (zákazník ${project.customer})` : ''}. Pre úlohu bez projektu prepni Projekt na „(bez projektu)“.` : 'Úloha bez projektu — zákazníka môžeš vybrať zo zoznamu alebo napísať nového.'}</p>
      </div>}
      <datalist id="j-customers">{customers.map(c => <option key={c.id} value={c.name} />)}</datalist>
      <div>
        <span className="j-lbl">Kedy</span>
        <div className="j-time-row">
          <input type="date" value={date} aria-label="Dátum aktivity" onChange={e => setDate(e.target.value)} />
          <label className="j-t"><span>od</span><input type="time" value={from} onChange={e => setFrom(e.target.value)} onKeyDown={e => e.key === 'Enter' && void save()} /></label>
          <label className="j-t"><span>do</span><input type="time" value={to} onChange={e => setTo(e.target.value)} onKeyDown={e => e.key === 'Enter' && void save()} /></label>
        </div>
        <div className="j-span"><SpanText date={date || defaultDate} from={from} to={to} /></div>
      </div>
      <div>
        <label className="j-lbl" htmlFor="j-text">Čo si robil / robíš</label>
        <textarea id="j-text" ref={textRef} rows={4} value={text} maxLength={5000} onChange={e => setText(e.target.value)}
          placeholder={'napr.\n– nasadil monitoring na srv01\n– vyriešil ticket #42\n– porada s dodávateľom'}
          onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void save(); } }} />
        <div className="j-write-row">
          <p className={'j-hint' + (hint?.warn ? ' warn' : '')}>{hint?.text ?? 'Enter = nový riadok · Ctrl+Enter = zapísať. Cez polnoc: dátum = deň začiatku, napr. od 23:00 do 01:30.'}</p>
          <button className="primary" onClick={() => void save()}>Zapísať</button>
        </div>
      </div>
    </div>
  </section>;
}

/** Úprava aktivity v zozname aj v detaile úlohy (buildActEditor). */
export function ActivityEditor({ a, tasks, projects, onDone, onError }: { a: Activity; tasks: Task[]; projects: Project[]; onDone: () => void; onError: (m: string) => void }) {
  const run = useRun(onError);
  const [taskId, setTaskId] = useState<number | ''>(a.task_id ?? '');
  const [date, setDate] = useState(a.date);
  const [from, setFrom] = useState(a.start);
  const [to, setTo] = useState(a.end ?? '');
  const [text, setText] = useState(a.text);
  const ta = useRef<HTMLTextAreaElement>(null);
  useEffect(() => { const t = ta.current; if (t) { t.focus(); t.setSelectionRange(t.value.length, t.value.length); } }, []);
  const err = !date ? 'Zadaj dátum.' : !from && to ? 'Zadaj aj čas „od“.' : '';
  const groups = [...projects.slice().sort(byName).map(p => ({ label: p.name + (p.customer ? ' — ' + p.customer : ''), id: p.id as number | null })), { label: 'Bez projektu', id: null }]
    .map(g => ({ ...g, tasks: tasks.filter(t => (t.project_id ?? null) === g.id && ((t.active && !t.done) || t.id === a.task_id)).sort(byName) }))
    .filter(g => g.tasks.length);
  async function save() {
    if (err) return;
    if (!text.trim()) { ta.current?.focus(); return; }
    if (!taskId) return;
    const r = await run(() => journalApi.editActivity(a.id, { task_id: Number(taskId), date, start: from || null, end: to || null, text }));
    if (r) onDone();
  }
  const keys = (e: KeyboardEvent, isText = false) => {
    if (e.key === 'Escape') { e.preventDefault(); onDone(); }
    else if (e.key === 'Enter' && (e.ctrlKey || e.metaKey || !isText)) { e.preventDefault(); void save(); }
  };
  return <div className="j-act-ed" onClick={e => e.stopPropagation()}>
    <select aria-label="Úloha" value={taskId} onChange={e => setTaskId(e.target.value ? Number(e.target.value) : '')} onKeyDown={e => keys(e)}>
      {!a.task_id && <option value="">— bez úlohy —</option>}
      {groups.map(g => <optgroup key={g.label} label={g.label}>{g.tasks.map(t => <option key={t.id} value={t.id}>{taskLabel(t)}{t.done ? ' (hotová)' : ''}</option>)}</optgroup>)}
    </select>
    <div className="j-time-row">
      <input type="date" aria-label="Dátum" value={date} onChange={e => setDate(e.target.value)} onKeyDown={e => keys(e)} />
      <label className="j-t"><span>od</span><input type="time" aria-label="Čas od" value={from} onChange={e => setFrom(e.target.value)} onKeyDown={e => keys(e)} /></label>
      <label className="j-t"><span>do</span><input type="time" aria-label="Čas do" value={to} onChange={e => setTo(e.target.value)} onKeyDown={e => keys(e)} /></label>
    </div>
    <div className={'j-span' + (err ? ' warn' : '')}>{err || (from && to ? <SpanText date={date} from={from} to={to} /> : 'Bez času „do“ — trvanie sa nepočíta.')}</div>
    <textarea ref={ta} aria-label="Text aktivity" value={text} maxLength={5000} rows={Math.min(8, Math.max(3, text.split('\n').length + 1))} onChange={e => setText(e.target.value)} onKeyDown={e => keys(e, true)} />
    <div className="j-acts"><span className="j-kb">Ctrl+Enter uložiť · Esc zrušiť</span>
      <button className="secondary compact" onClick={onDone}>Zrušiť</button>
      <button className="primary compact" disabled={!!err || !taskId} onClick={() => void save()}>Uložiť</button></div>
  </div>;
}

export const timeText = (a: Activity) => a.end ? `${a.start}–${a.end}${a.end_next_day ? ' +1' : ''}` : a.start;

/** „Posledné aktivity“: 21 posledných dní s aktivitou (renderLog). */
export function ActivityLog({ uid, onOpenTask, onError }: { uid: number | null; onOpenTask: (id: number) => void; onError: (m: string) => void }) {
  const run = useRun(onError);
  const acts = useRecentActivities(uid);
  const tasks = useTasks(uid).data ?? [];
  const projects = useProjects().data ?? [];
  const [editing, setEditing] = useState<number | null>(null);
  if (acts.isPending) return <section className="j-card"><h2>Posledné aktivity</h2><p className="j-empty">Načítavam…</p></section>;
  const byDay = new Map<string, Activity[]>();
  for (const a of acts.data ?? []) byDay.set(a.date, [...(byDay.get(a.date) ?? []), a]);
  const days = [...byDay.keys()].sort().reverse().slice(0, 21);
  return <section className="j-card">
    <h2>Posledné aktivity</h2>
    {!days.length && <p className="j-empty">Zatiaľ žiadne aktivity. Vyber projekt a úlohu hore a napíš, čo robíš.</p>}
    {days.map(k => {
      const items = byDay.get(k)!.slice().sort((x, y) => y.start_at.localeCompare(x.start_at));
      const mins = items.reduce((n, a) => n + (a.minutes ?? 0), 0);
      return <div className="j-log-day" key={k}>
        <h3><span>{prettyDate(k)}</span>{mins > 0 && <span className="j-d-total">{mins} min</span>}</h3>
        {items.map(a => editing === a.id
          ? <ActivityEditor key={a.id} a={a} tasks={tasks} projects={projects} onDone={() => setEditing(null)} onError={onError} />
          : <div className="j-log-item" key={a.id}>
            <div className="j-log-time">{timeText(a)}</div>
            <div className="j-log-body">
              <div className="j-log-ctx">
                {a.project && <span className="j-tag proj">{a.project}</span>}
                {a.customer && <span className="j-tag cust">{a.customer}</span>}
                {a.task_id ? <button className="j-tag task link" title="Zobraziť všetky aktivity tejto úlohy" onClick={() => onOpenTask(a.task_id!)}>{a.task}</button> : <span className="j-tag task">—</span>}
              </div>
              <div className="j-log-text">{a.text}</div>
            </div>
            {a.minutes != null && <div className="j-log-min">{a.minutes}m</div>}
            <div className="j-log-acts">
              <button className="icon-button" aria-label="Upraviť aktivitu" title="Upraviť aktivitu" onClick={() => setEditing(a.id)}><Pencil size={15} /></button>
              <button className="icon-button" aria-label="Zmazať aktivitu" title="Zmazať aktivitu" onClick={() => { if (window.confirm('Zmazať túto aktivitu?')) void run(() => journalApi.deleteActivity(a.id)); }}><X size={16} /></button>
            </div>
          </div>)}
      </div>;
    })}
  </section>;
}


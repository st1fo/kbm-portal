import { ChevronRight, Plus, Power, Trash2, X } from 'lucide-react';
import { Fragment, useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import type { Project, Task } from './api';
import { journalApi } from './api';
import { DAY_NAMES, baNow, fmtDur, fmtShort, plural, weekday } from './calc';
import { ActivityEditor, timeText } from './Activities';
import type { Prefill } from './Activities';
import { useOptimisticFlags, useProjects, useRun, useTaskActivities, useTasks } from './hooks';

const TASK_PREVIEW = 8;
const shortDate = (k: string) => `${DAY_NAMES[weekday(k)]} ${Number(k.slice(8))}.${Number(k.slice(5, 7))}.${k.slice(0, 4) !== baNow().date.slice(0, 4) ? k.slice(0, 4) : ''}`;

function Highlight({ text, q }: { text: string; q: string }) {
  if (!q) return <>{text}</>;
  const parts: ReactNode[] = [];
  const lo = text.toLowerCase(), ql = q.toLowerCase();
  let i = 0, j: number;
  while ((j = lo.indexOf(ql, i)) >= 0) { parts.push(text.slice(i, j), <mark key={j}>{text.slice(j, j + q.length)}</mark>); i = j + q.length; }
  parts.push(text.slice(i));
  return <>{parts}</>;
}

/** Rozkliknutá úloha: aktivity, filter, „+ Zapísať k tejto úlohe“ (buildTaskDetail). */
function TaskDetail({ task, uid, tasks, projects, onWrite, onError }: { task: Task; uid: number | null; tasks: Task[]; projects: Project[]; onWrite: (p: Omit<Prefill, 'nonce'>) => void; onError: (m: string) => void }) {
  const acts = useTaskActivities(task.id, uid);
  const [filter, setFilter] = useState('');
  const [all, setAll] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const list = acts.data ?? [];
  const q = filter.trim();
  const shown = q ? list.filter(a => a.text.toLowerCase().includes(q.toLowerCase())) : list;
  const lim = all || q ? shown.length : TASK_PREVIEW;
  const mins = list.reduce((n, a) => n + (a.minutes ?? 0), 0);
  return <li className="j-task-detail">
    <div className="j-tdet-head">
      <div className="j-tdet-sum">{list.length
        ? <><strong>{list.length} {plural(list.length, 'aktivita', 'aktivity', 'aktivít')}</strong>{mins ? ' · ' + fmtDur(mins) : ''} · {shortDate(list[list.length - 1].date)}{list.length > 1 ? ' – ' + shortDate(list[0].date) : ''}</>
        : acts.isPending ? 'Načítavam…' : 'Zatiaľ žiadne aktivity.'}</div>
      <button className="secondary compact" onClick={() => task.done
        ? onWrite({ projectId: task.project_id ?? '', taskId: '', hint: 'Úloha je označená ako hotová — odškrtni ju, ak k nej chceš zapisovať.', warn: true })
        : onWrite({ projectId: task.project_id ?? '', taskId: task.id, hint: `Úloha „${task.name}“ je vybraná. Napíš, čo si robil, a daj Zapísať (Ctrl+Enter).` })}>
        <Plus size={14} />Zapísať k tejto úlohe</button>
    </div>
    {list.length > 4 && <input className="j-tdet-filter" placeholder="Hľadať v aktivitách tejto úlohy…" aria-label="Hľadať v aktivitách" value={filter} onChange={e => setFilter(e.target.value)} />}
    {q && !shown.length && <p className="j-empty-small">Nič nenájdené pre „{q}“.</p>}
    {shown.slice(0, lim).map(a => editing === a.id
      ? <ActivityEditor key={a.id} a={a} tasks={tasks} projects={projects} onDone={() => setEditing(null)} onError={onError} />
      : <div className="j-tdet-act" key={a.id}>
        <div className="j-tdet-when"><span className="d">{shortDate(a.date)}</span><span>{timeText(a)}</span>{a.minutes != null && <span className="m">{fmtDur(a.minutes)}</span>}
          <button className="icon-button" aria-label="Upraviť aktivitu" onClick={() => setEditing(a.id)}>✎</button></div>
        <div className="j-tdet-text"><Highlight text={a.text} q={q} /></div>
      </div>)}
    {shown.length > lim && <button className="text-button" onClick={() => setAll(true)}>Zobraziť všetky ({shown.length})</button>}
  </li>;
}

/** Zmazanie (admin); ak na záznam niečo odkazuje, server vráti 409 a ponúkne sa deaktivácia. */
async function removeOrDeactivate(name: string, del: () => Promise<unknown>, deactivate: () => Promise<unknown>, run: <T>(fn: () => Promise<T>) => Promise<T | undefined>) {
  if (!window.confirm(`Zmazať „${name}“?`)) return;
  try { await del(); await run(async () => null); }
  catch (e) { if (window.confirm((e as Error).message + '\n\nDeaktivovať teraz? Zostane v histórii, ale nebude ho možné vybrať.')) await run(deactivate); }
}

/** „Projekty a úlohy“: zdieľaný katalóg s rozkliknutím úloh (renderProjectManager). */
export function ProjectManager({ uid, isAdmin, openTask, onWrite, onError }: {
  uid: number | null; isAdmin: boolean; openTask: { id: number; nonce: number } | null; onWrite: (p: Omit<Prefill, 'nonce'>) => void; onError: (m: string) => void;
}) {
  const run = useRun(onError);
  const [showInactive, setShowInactive] = useState(false);
  const projects = useProjects(showInactive).data ?? [];
  const tasks = useTasks(uid, showInactive).data ?? [];
  const [open, setOpen] = useState<Set<number>>(new Set());
  const flags = useOptimisticFlags();
  useEffect(() => {
    if (!openTask) return;
    setOpen(o => new Set(o).add(openTask.id));
    setTimeout(() => {
      const li = document.querySelector<HTMLElement>(`li[data-task="${openTask.id}"]`);
      if (li) { li.scrollIntoView({ behavior: 'smooth', block: 'center' }); li.classList.remove('j-flash'); void li.offsetWidth; li.classList.add('j-flash'); }
    }, 50);
  }, [openTask?.nonce]); // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = (id: number) => setOpen(o => { const n = new Set(o); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const groups = [...projects.slice().sort((a, b) => a.name.localeCompare(b.name, 'sk')).map(p => ({ project: p as Project | null, tasks: tasks.filter(t => t.project_id === p.id) })),
    { project: null, tasks: tasks.filter(t => !t.project_id) }].filter(g => g.project || g.tasks.length);
  return <section className="j-card">
    <div className="j-card-head"><h2>Projekty a úlohy</h2>
      {isAdmin && <label className="check-label j-small"><input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} />aj neaktívne</label>}</div>
    {!groups.length && <p className="j-empty">Zatiaľ žiadne projekty. Pridaj prvý tlačidlom + pri poli Projekt.</p>}
    {groups.map(g => {
      const active = g.tasks.filter(t => !t.done).length;
      const count = g.tasks.reduce((n, t) => n + t.activity_count, 0), mins = g.tasks.reduce((n, t) => n + t.minutes, 0);
      return <div className="j-proj" key={g.project?.id ?? 'none'}>
        <div className="j-proj-row1">
          <span className={'j-proj-name' + (g.project && !g.project.active ? ' inactive' : '')}>{g.project ? g.project.name : 'Bez projektu'}</span>
          {g.project?.customer && <span className="j-proj-cust">{g.project.customer}</span>}
          {g.project && !g.project.active && <span className="badge rejected">neaktívny</span>}
          {isAdmin && g.project && (g.project.active
            ? <button className="icon-button" aria-label="Zmazať projekt" title="Zmazať alebo deaktivovať projekt"
              onClick={() => void removeOrDeactivate(g.project!.name, () => journalApi.deleteProject(g.project!.id), () => journalApi.editProject(g.project!.id, { active: false }), run)}><Trash2 size={15} /></button>
            : <button className="icon-button" aria-label="Aktivovať projekt" title="Aktivovať" onClick={() => void run(() => journalApi.editProject(g.project!.id, { active: true }))}><Power size={15} /></button>)}
        </div>
        <div className="j-proj-meta">{g.tasks.length ? `${active} ${plural(active, 'aktívna', 'aktívne', 'aktívnych')} z ${g.tasks.length} ${plural(g.tasks.length, 'úlohy', 'úloh', 'úloh')}` : 'žiadne úlohy'}
          {count ? ` · ${count} ${plural(count, 'aktivita', 'aktivity', 'aktivít')}${mins ? ' · ' + fmtDur(mins) : ''}` : ''}</div>
        {g.tasks.length > 0 && <ul className="j-tasks">
          {g.tasks.slice().sort((a, b) => Number(a.done) - Number(b.done) || a.name.localeCompare(b.name, 'sk')).map(t => {
            const isOpen = open.has(t.id);
            return <Fragment key={t.id}>
              <li data-task={t.id} className={isOpen ? 'open' : ''}>
                <button className={'j-tm-toggle' + (isOpen ? ' open' : '')} aria-expanded={isOpen} aria-label={(isOpen ? 'Zbaliť' : 'Rozbaliť') + ' aktivity úlohy ' + t.name} onClick={() => toggle(t.id)}><ChevronRight size={14} /></button>
                <input type="checkbox" className="j-check" checked={flags.get(t.id, t.done)} aria-label={'Hotová úloha ' + t.name} onChange={e => { const v = e.target.checked; void flags.toggle(t.id, v, () => run(() => journalApi.editTask(t.id, { done: v }))); }} />
                <span className={'j-tm-name' + (t.done ? ' done' : '') + (!t.active ? ' inactive' : '')} title="Klikni pre aktivity tejto úlohy" onClick={() => toggle(t.id)}>{t.name}</span>
                <span className="j-tm-stat">{t.activity_count ? `${t.activity_count}×${t.minutes ? ' · ' + fmtShort(t.minutes) : ''}` : ''}</span>
                {!t.project_id && <button className={'j-tm-cust' + (t.customer ? '' : ' empty')} title={t.customer ? 'Zmeniť zákazníka' : 'Priradiť zákazníka'} onClick={() => {
                  const v = window.prompt(`Zákazník pre úlohu „${t.name}“ (prázdne = bez zákazníka):`, t.customer);
                  if (v !== null) void run(() => journalApi.editTask(t.id, { customer_name: v.trim() || null }));
                }}>{t.customer || '+ zákazník'}</button>}
                {isAdmin && (t.active
                  ? <button className="icon-button" aria-label="Zmazať úlohu" title="Zmazať alebo deaktivovať úlohu" onClick={() => void removeOrDeactivate(t.name, () => journalApi.deleteTask(t.id), () => journalApi.editTask(t.id, { active: false }), run)}><X size={15} /></button>
                  : <button className="icon-button" aria-label="Aktivovať úlohu" title="Aktivovať" onClick={() => void run(() => journalApi.editTask(t.id, { active: true }))}><Power size={14} /></button>)}
              </li>
              {isOpen && <TaskDetail task={t} uid={uid} tasks={tasks} projects={projects} onWrite={onWrite} onError={onError} />}
            </Fragment>;
          })}
        </ul>}
      </div>;
    })}
  </section>;
}

import { CalendarRange, FolderKanban, SquareKanban, Sun, Upload } from 'lucide-react';
import { useCallback, useEffect, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import type { User } from '../api';
import { ActivityForm, ActivityLog } from './Activities';
import type { Prefill } from './Activities';
import { baNow } from './calc';
import { CommandBar } from './CommandBar';
import { DayCard } from './DayCard';
import { useJournalState } from './hooks';
import { ImportDialog } from './ImportDialog';
import { MonthOverview, ProjectStats } from './MonthOverview';
import { ProjectManager } from './Projects';
import { usePunchQueue } from './punchQueue';
import { BoardsPage } from './Board';
import './journal.css';

type Tab = 'today' | 'todo' | 'projects' | 'month';
const TABS: [Tab, string, typeof Sun, string][] = [
  ['today', 'Dnes', Sun, 'dochádzka a aktivity'],
  ['todo', 'Nástenky', SquareKanban, 'úlohy na kartách'],
  ['projects', 'Projekty', FolderKanban, 'zákazníci, úlohy, čas'],
  ['month', 'Mesiac', CalendarRange, 'odpracované dni'],
];

/** Sekcia Denník: skutočný odpracovaný čas, aktivity na úlohách a nástenky úloh. */
export function JournalPage({ me, users, isAdmin, notify }: { me: User; users: User[]; isAdmin: boolean; notify: (m: string) => void }) {
  const qc = useQueryClient();
  const [viewUid, setViewUid] = useState<number | null>(null);   // null = vlastný denník; admin môže prezerať iného
  const uid = viewUid;
  const state = useJournalState(uid, me.id);
  const q = usePunchQueue(me.id, qc);
  const [error, setError] = useState('');
  const onError = useCallback((m: string) => setError(m), []);
  const [attDate, setAttDate] = useState<string | null>(null);  // null = sleduje aktívny deň
  const [month, setMonth] = useState(() => baNow().date.slice(0, 7));
  const [prefill, setPrefill] = useState<Prefill | null>(null);
  const [openTask, setOpenTask] = useState<{ id: number; nonce: number } | null>(null);
  const [importing, setImporting] = useState(false);
  const [tab, setTabState] = useState<Tab>(() => { try { const t = sessionStorage.getItem('kbm-journal-tab'); return TABS.some(x => x[0] === t) ? t as Tab : 'today'; } catch { return 'today'; } });
  const setTab = (t: Tab) => { setTabState(t); try { sessionStorage.setItem('kbm-journal-tab', t); } catch { /* bez úložiska */ } };
  useEffect(() => { setAttDate(null); setPrefill(null); }, [viewUid]);
  const date = attDate ?? state.data?.active_day ?? baNow().date;
  const write = (p: Omit<Prefill, 'nonce'>) => { setPrefill({ ...p, nonce: Date.now() }); setTab('today'); };
  const viewed = users.find(u => u.id === viewUid);
  const shownError = error || q.error;

  return <div className="j-page">
    {isAdmin && <div className="j-viewer">
      <label>Denník zamestnanca
        <select value={viewUid ?? ''} onChange={e => setViewUid(e.target.value && Number(e.target.value) !== me.id ? Number(e.target.value) : null)}>
          <option value="">{me.name} (môj)</option>
          {users.filter(u => u.active && u.id !== me.id).map(u => <option key={u.id} value={u.id}>{u.name}</option>)}
        </select></label>
      {viewed && <span className="j-viewer-note">Prezeráte denník používateľa {viewed.name}. Pípať môže iba on sám; dni, aktivity a jeho nástenky môžete opraviť (zmeny sa zapíšu do auditu).</span>}
    </div>}
    {shownError && <div role="alert" className="error">{shownError}<button onClick={() => { setError(''); q.clearError(); }}>Zavrieť</button></div>}
    {state.isError && <div role="alert" className="error">Stav dochádzky sa nepodarilo načítať. <button onClick={() => void state.refetch()}>Skúsiť znova</button></div>}
    {!viewUid && <CommandBar state={state.data} queue={q.queue} problem={q.problem} skewMin={state.data?.skew_min ?? 0} onPunch={q.punch} onRetry={() => void q.retry()} />}
    <nav className="j-tabs" data-tour="journal-tabs" aria-label="Časti denníka">
      {TABS.map(([key, label, Icon, hint]) => <button key={key} className={tab === key ? 'on' : ''} aria-pressed={tab === key} onClick={() => setTab(key)}><Icon size={18} /><span>{label}<small>{hint}</small></span></button>)}
    </nav>
    {tab === 'today' && <>
      <DayCard uid={uid} date={date} setDate={d => setAttDate(d)} state={state.data} onError={onError} />
      <div className="j-dash">
        <div className="j-col"><ActivityForm uid={uid} defaultDate={state.data?.active_day ?? baNow().date} prefill={prefill} onError={onError} /></div>
        <div className="j-col"><ActivityLog uid={uid} onOpenTask={id => { setOpenTask({ id, nonce: Date.now() }); setTab('projects'); }} onError={onError} /></div>
      </div>
    </>}
    {tab === 'todo' && <BoardsPage uid={uid} me={me} users={users} onWrite={write} onError={onError} />}
    {tab === 'projects' && <div className="j-dash">
      <div className="j-col"><ProjectManager uid={uid} isAdmin={isAdmin} openTask={openTask} onWrite={write} onError={onError} /></div>
      <div className="j-col"><ProjectStats uid={uid} month={month} /></div>
    </div>}
    {tab === 'month' && <div className="j-single"><MonthOverview uid={uid} month={month} setMonth={setMonth} attDate={date} setAttDate={d => setAttDate(d)} onError={onError} /></div>}
    {!viewUid && tab === 'month' && <div className="j-foot">
      <span className="muted">Dáta Denníka sú uložené na serveri Workplanu. Pípnutia bez spojenia počkajú v prehliadači a odošlú sa neskôr.</span>
      <button className="secondary compact" onClick={() => setImporting(true)}><Upload size={15} />Importovať zo starého denníka</button>
    </div>}
    {importing && <ImportDialog onClose={() => setImporting(false)} onDone={m => { setImporting(false); notify(m); }} />}
  </div>;
}

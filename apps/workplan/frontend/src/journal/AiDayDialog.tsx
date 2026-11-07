import { AlertTriangle, Loader2, Plus, Send, Sparkles, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Dialog } from '../components/ui/dialog';
import type { AiChecked, AiItem, AiRange } from './api';
import { journalApi } from './api';
import { fmtDur } from './calc';
import { useProjects, useTasks } from './hooks';

type Row = AiItem & Partial<Pick<AiChecked, 'errors' | 'warnings' | 'minutes' | 'next_day'>> & { key: number };
type Msg = { role: 'user' | 'model'; text: string };
let seq = 0;
const blank = (start = '', end = ''): Row => ({ key: ++seq, start, end, task_id: null, new_task: null, project_id: null, customer: null, text: '' });
const plain = (r: Row): AiItem => ({ start: r.start, end: r.end, task_id: r.task_id, new_task: r.task_id ? null : (r.new_task?.trim() || null), project_id: r.task_id ? null : r.project_id, customer: r.task_id ? null : r.customer, text: r.text });
const ranges = (xs: AiRange[]) => xs.map(x => `${x.start}–${x.end}${x.next_day ? ' (+1)' : ''}`).join(', ');

/** AI doplnenie dňa: text → návrh aktivít v tabuľke → kontrola → zápis. Gemini nič neukladá. */
export function AiDayDialog({ uid, initialDate, onClose, onDone }: { uid: number | null; initialDate: string; onClose: () => void; onDone: (msg: string) => void }) {
  const qc = useQueryClient();
  const [date, setDate] = useState(initialDate);
  const day = useQuery({ queryKey: ['journal', 'aiDay', uid, date], queryFn: () => journalApi.aiDay(date, uid), enabled: !!date });
  const tasks = (useTasks(uid).data ?? []).filter(t => t.active);
  const projects = (useProjects().data ?? []).filter(p => p.active);
  const [text, setText] = useState('');
  const [history, setHistory] = useState<Msg[]>([]);
  const [rows, setRows] = useState<Row[]>([]);
  const [busy, setBusy] = useState<'' | 'ai' | 'save'>('');
  const [error, setError] = useState('');
  const checkTimer = useRef<number | undefined>(undefined);
  const d = day.data;

  useEffect(() => { setRows([]); setHistory([]); setError(''); }, [date]);
  // ručné úpravy tabuľky priebežne kontroluje server (prekryvy, úlohy, pípnutia)
  const sig = JSON.stringify(rows.map(plain));
  useEffect(() => {
    window.clearTimeout(checkTimer.current);
    const ready = rows.filter(r => r.start && r.end);
    if (!ready.length) return;
    checkTimer.current = window.setTimeout(async () => {
      try {
        const res = await journalApi.aiCheck(date, ready.map(plain), uid);
        setRows(rs => { let i = 0; return rs.map(r => r.start && r.end ? { ...r, ...pick(res[i++]) } : r); });
      } catch { /* kontrola sa zopakuje pri ďalšej zmene */ }
    }, 450);
    return () => window.clearTimeout(checkTimer.current);
  }, [sig, date]); // eslint-disable-line react-hooks/exhaustive-deps

  const pick = (c?: AiChecked) => c ? { errors: c.errors, warnings: c.warnings, minutes: c.minutes, next_day: c.next_day } : {};
  async function ask() {
    const msg = text.trim();
    if (!msg || busy) return;
    setBusy('ai'); setError('');
    try {
      const r = await journalApi.aiPropose({ date, message: msg, history: history.slice(-18), draft: rows.filter(x => x.start && x.end).map(plain) }, uid);
      setHistory(h => [...h, { role: 'user', text: msg }, { role: 'model', text: r.reply }]);
      setRows(r.items.map(i => ({ ...i, key: ++seq })));
      setText('');
    } catch (e) { setError((e as Error).message); }
    setBusy('');
  }
  async function save() {
    setBusy('save'); setError('');
    try {
      const r = await journalApi.aiApply(date, rows.map(plain), uid);
      await qc.invalidateQueries({ queryKey: ['journal'] });
      onDone(`Zapísané: ${r.created} ${r.created === 1 ? 'aktivita' : r.created < 5 ? 'aktivity' : 'aktivít'}${r.new_tasks.length ? `, nové úlohy: ${r.new_tasks.join(', ')}` : ''}.`);
    } catch (e) { setError((e as Error).message); setBusy(''); }
  }
  const set = (key: number, patch: Partial<Row>) => setRows(rs => rs.map(r => r.key === key ? { ...r, ...patch } : r));
  const addRow = () => {
    const used = rows.map(r => r.end).filter(Boolean).sort();
    const free = d?.free.find(f => !rows.some(r => r.start === f.start));
    setRows(rs => [...rs, blank(free?.start ?? used[used.length - 1] ?? '', free?.end ?? '')]);
  };
  const invalid = !rows.length || rows.some(r => !r.start || !r.end || (r.errors?.length ?? 0) > 0 || (!r.task_id && !r.new_task?.trim()) || !r.text.trim());
  const total = rows.reduce((s, r) => s + (r.minutes ?? 0), 0);
  const byProject = new Map<string, typeof tasks>();
  for (const t of tasks.slice().sort((a, b) => Number(a.done) - Number(b.done) || a.name.localeCompare(b.name, 'sk')))
    byProject.set(t.project || t.customer || 'Bez projektu', [...(byProject.get(t.project || t.customer || 'Bez projektu') ?? []), t]);
  const close = () => { if (!rows.length || window.confirm('Zahodiť rozpracovaný návrh?')) onClose(); };

  return <Dialog title="Doplniť deň s AI" onClose={close} wide>
    <div className="ai-day">
      <div className="ai-top">
        <label className="ai-date">Deň<input type="date" value={date} onChange={e => e.target.value && setDate(e.target.value)} /></label>
        {d && <div className="ai-sum">
          <span><b>{fmtDur(d.worked_min)}</b> odpracované{d.worked.length ? <small>{ranges(d.worked)}</small> : <small>bez pípnutí</small>}</span>
          <span><b>{fmtDur(d.logged_min)}</b> v aktivitách{d.logged.length > 0 && <small>{d.logged.length} {d.logged.length === 1 ? 'zápis' : 'zápisy'}</small>}</span>
          <span className={d.free_min ? 'free' : 'ok'}><b>{fmtDur(d.free_min)}</b> nezapísané{d.free.length > 0 && <small>{ranges(d.free)}</small>}</span>
        </div>}
      </div>
      {d && !d.configured && <p className="ai-note"><AlertTriangle size={15} />AI nie je na serveri nastavené (chýba kľúč Gemini). Aktivity môžeš doplniť ručne v tabuľke nižšie.</p>}
      {history.length > 0 && <div className="ai-chat" aria-live="polite">{history.map((m, i) => <p key={i} className={'ai-msg ' + m.role}>{m.text}</p>)}</div>}
      {d?.configured !== false && <div className="ai-ask">
        <textarea rows={3} value={text} maxLength={4000} disabled={busy === 'ai'} aria-label="Čo si robil"
          placeholder={rows.length ? 'Uprav návrh, napr. „call daj na 15 minút, zvyšok tmavý režim“' : 'Čo si v tento deň robil? Napr. „ráno call so Zdenom, potom tmavý režim a nasadenie na server“'}
          onChange={e => setText(e.target.value)} onKeyDown={e => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void ask(); } }} />
        <button className="primary" disabled={!text.trim() || !!busy} onClick={() => void ask()}>
          {busy === 'ai' ? <Loader2 size={16} className="spin" /> : rows.length ? <Send size={16} /> : <Sparkles size={16} />}{rows.length ? 'Upraviť návrh' : 'Navrhnúť'}</button>
      </div>}
      {error && <div role="alert" className="error">{error}<button onClick={() => setError('')}>Zavrieť</button></div>}
      {rows.length > 0 && <div className="ai-rows">
        {rows.map(r => <div key={r.key} className={'ai-row' + (r.errors?.length ? ' bad' : '')}>
          <div className="ai-row-main">
            <div className="ai-time">
              <input type="time" value={r.start} aria-label="Od" onChange={e => set(r.key, { start: e.target.value })} />
              <span>–</span>
              <input type="time" value={r.end} aria-label="Do" onChange={e => set(r.key, { end: e.target.value })} />
              {r.minutes != null && <small>{fmtDur(r.minutes)}{r.next_day ? ' · do rána' : ''}</small>}
            </div>
            <div className="ai-task">
              <select aria-label="Úloha" value={r.task_id ? String(r.task_id) : 'new'} onChange={e => set(r.key, e.target.value === 'new' ? { task_id: null, new_task: r.new_task ?? '' } : { task_id: Number(e.target.value) })}>
                <option value="new">＋ nová úloha…</option>
                {[...byProject].map(([g, ts]) => <optgroup key={g} label={g}>{ts.map(t => <option key={t.id} value={t.id}>{t.name}{t.done ? ' (hotová)' : ''}</option>)}</optgroup>)}
              </select>
              {!r.task_id && <div className="ai-new">
                <input value={r.new_task ?? ''} maxLength={200} placeholder="Názov novej úlohy" aria-label="Názov novej úlohy" onChange={e => set(r.key, { new_task: e.target.value })} />
                <select aria-label="Projekt novej úlohy" value={r.project_id ?? ''} onChange={e => set(r.key, { project_id: e.target.value ? Number(e.target.value) : null })}>
                  <option value="">{r.customer ? 'Zákazník: ' + r.customer : 'Bez projektu'}</option>
                  {projects.map(p => <option key={p.id} value={p.id}>{p.name}{p.customer ? ' — ' + p.customer : ''}</option>)}
                </select>
                <span className="ai-badge">nová</span>
              </div>}
            </div>
            <button className="icon-button" aria-label="Odstrániť riadok" onClick={() => setRows(rs => rs.filter(x => x.key !== r.key))}><Trash2 size={16} /></button>
          </div>
          <textarea rows={2} value={r.text} maxLength={5000} placeholder="Čo si robil" aria-label="Popis" onChange={e => set(r.key, { text: e.target.value })} />
          {(r.errors?.length || r.warnings?.length) ? <p className="ai-issues">{r.errors?.map(x => <span key={x} className="err">{x}</span>)}{r.warnings?.map(x => <span key={x} className="warn">{x}</span>)}</p> : null}
        </div>)}
      </div>}
      <div className="dialog-actions ai-actions">
        <button className="secondary compact" onClick={addRow}><Plus size={15} />Pridať riadok</button>
        <span className="ai-total">{rows.length > 0 && <>Spolu <b>{fmtDur(total)}</b>{d && d.free_min > 0 && <> z nezapísaných {fmtDur(d.free_min)}</>}</>}</span>
        <button className="secondary compact" onClick={close}>Zrušiť</button>
        <button className="primary compact" disabled={invalid || !!busy} onClick={() => void save()}>
          {busy === 'save' && <Loader2 size={15} className="spin" />}Zapísať {rows.length || ''} {rows.length === 1 ? 'aktivitu' : rows.length > 1 && rows.length < 5 ? 'aktivity' : 'aktivít'}</button>
      </div>
    </div>
  </Dialog>;
}

import { ChevronLeft, ChevronRight, Plus, Trash2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { JState } from './api';
import { journalApi } from './api';
import { addDays, calc, fmtDur, minToHm, minutesSince, plural, prettyDate, ratioInfo, unwrap, validate } from './calc';
import { useDay, useNow, useOverview, useRun } from './hooks';
import { SegmentRows, cleanRows, rowsOf } from './SegmentRows';
import type { DraftRow } from './SegmentRows';

/** Karta „Dochádzka“: úseky zvoleného dňa, ručná úprava a súčty (renderAttendance). */
export function DayCard({ uid, date, setDate, state, onError }: {
  uid: number | null; date: string; setDate: (d: string) => void; state: JState | undefined; onError: (m: string) => void;
}) {
  const run = useRun(onError);
  const dayQ = useDay(date, uid);
  const overview = useOverview(date.slice(0, 7), uid);
  const isActive = !!state && state.active_day === date;
  const source = isActive ? state!.day : dayQ.data;
  const live = isActive && !!source && !source.done && source.segments.length > 0;
  const now = useNow(live ? 1000 : 60000);
  const [rows, setRows] = useState<DraftRow[]>([]);
  const [hint, setHint] = useState<{ text: string; warn: boolean } | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const sig = JSON.stringify(source?.segments ?? []) + date;
  useEffect(() => {
    if (box.current?.contains(document.activeElement)) return;  // neprepisovať práve upravovaný úsek
    setRows(rowsOf(source?.segments ?? [])); setHint(null);
  }, [sig]); // eslint-disable-line react-hooks/exhaustive-deps

  const clean = cleanRows(rows);
  const nowMin = minutesSince(date, now);
  const c = calc(clean, live, nowMin);
  const u = unwrap(clean);
  const lastOut = [...u].reverse().find(x => x.out != null)?.out ?? null;
  const logged = overview.data?.days.find(d => d.date === date)?.logged_min ?? 0;
  const ri = ratioInfo(logged, c.work);
  const err = validate(clean);

  async function commit(next: DraftRow[]) {
    const segs = cleanRows(next);
    const e = validate(segs);
    if (e) { setHint({ text: e, warn: true }); return; }
    if (await run(() => journalApi.saveDay(date, segs.map(r => ({ start: r.in, end: r.out })), uid))) setHint({ text: 'Uložené.', warn: false });
  }
  function addSegment() {
    const ls = clean[clean.length - 1];
    if (ls && ls.in && !ls.out && live) { setHint({ text: 'Posledný úsek ešte beží — najprv doplň jeho odchod.', warn: true }); return; }
    setRows([...rows, { in: '', out: '' }]);
    setTimeout(() => { const ins = box.current?.querySelectorAll<HTMLInputElement>('.j-seg-row input'); ins?.[ins.length - 2]?.focus(); }, 0);
  }
  async function deleteDay() {
    if (!source?.segments.length) { setHint({ text: 'Tento deň nemá žiadne zapísané časy.', warn: true }); return; }
    if (!window.confirm(`Vymazať dochádzku za ${prettyDate(date)}? Aktivity zostanú zachované.`)) return;
    await run(() => journalApi.deleteDay(date, uid));
  }
  const defaultHint = isActive
    ? 'Každý príchod–odchod je jeden úsek, medzery medzi nimi sa rátajú ako prestávka. Časy môžeš prepísať ručne; po polnoci zadaj čas normálne (napr. 02:00).'
    : `Upravuješ deň ${prettyDate(date)}. Zmeny sa uložia, keď opustíš pole alebo stlačíš Enter.`;

  return <section className="j-card">
    <div className="j-card-head">
      <h2>Dochádzka</h2>
      <div className="j-date-nav">
        <button className="j-mini" aria-label="Predchádzajúci deň" onClick={() => setDate(addDays(date, -1))}><ChevronLeft size={17} /></button>
        <input type="date" value={date} aria-label="Deň dochádzky" onChange={e => e.target.value && setDate(e.target.value)} />
        <button className="j-mini" aria-label="Nasledujúci deň" onClick={() => setDate(addDays(date, 1))}><ChevronRight size={17} /></button>
        <button className="j-mini j-del" aria-label="Vymazať tento deň" title="Vymazať tento deň" onClick={() => void deleteDay()}><Trash2 size={16} /></button>
      </div>
    </div>
    <div className="j-att-body">
      <div ref={box}>
        <SegmentRows rows={rows} onChange={setRows} onCommit={next => void commit(next)} liveMin={live ? nowMin : null}
          emptyText="Žiadne úseky. Pípni príchod alebo pridaj úsek ručne." />
        <button className="secondary compact j-seg-add" onClick={addSegment}><Plus size={15} />pridať úsek</button>
      </div>
      <div className="j-total-tile">
        <span className="j-k">Odpracované</span>
        <span className="j-v">{fmtDur(c.work)}</span>
        <span className="j-sub">prestávky {fmtDur(c.brk)}{c.breakRunning ? ' · beží' : ''}</span>
        {c.first != null && <span className="j-sub">{c.count} {plural(c.count, 'úsek', 'úseky', 'úsekov')} · {minToHm(c.first)}{lastOut != null && !live ? '–' + minToHm(lastOut) + (lastOut >= 1440 ? ' +1' : '') : ' – teraz'}</span>}
        {(logged > 0 || c.work > 0) && <span className={'j-sub j-logged ' + ri.cls}>odpísané v aktivitách {fmtDur(logged)}{c.work ? ` · ${ri.pct} %` : ''}</span>}
      </div>
    </div>
    <p className={'j-hint' + ((hint?.warn || err) ? ' warn' : '')}>{err || hint?.text || defaultHint}</p>
  </section>;
}

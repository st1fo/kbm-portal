import { ChevronLeft, ChevronRight, Pencil, Plus, Trash2 } from 'lucide-react';
import { Fragment, useState } from 'react';
import type { OverviewDay } from './api';
import { journalApi } from './api';
import { DAY_NAMES, MONTHS, baNow, calc, dayLabel, fmtDur, fmtShort, monthEnd, monthName, plural, ratioInfo, shiftMonth, validate, weekday } from './calc';
import { useOverview, useProjectStats, useRun } from './hooks';
import { SegmentRows, cleanRows, rowsOf } from './SegmentRows';
import type { DraftRow } from './SegmentRows';

function Meter({ logged, work }: { logged: number; work: number }) {
  const r = ratioInfo(logged, work);
  return <div className={'j-meter ' + r.cls} title={work ? `odpísané ${r.pct} % času v práci` : ''}><i style={{ width: Math.min(100, work ? r.pct : 0) + '%' }} /></div>;
}
function LoggedLine({ logged, work }: { logged: number; work: number }) {
  if (!work && !logged) return null;
  if (!work) return <>odpísané {fmtDur(logged)}</>;
  const r = ratioInfo(logged, work), miss = work - logged;
  return <>odpísané {fmtDur(logged)} · {r.pct} %{r.cls === 'over' ? <> · <span className="j-over">o {fmtDur(-miss)} viac</span></> : miss >= 1 ? <> · <span className={r.cls === 'low' ? 'j-low' : ''}>chýba {fmtDur(miss)}</span></> : null}</>;
}

/** Inline editor dňa v histórii (buildHistEditor): úpravy sa uložia až tlačidlom Uložiť. */
function HistoryEditor({ date, day, uid, onClose, onError }: { date: string; day: OverviewDay | undefined; uid: number | null; onClose: () => void; onError: (m: string) => void }) {
  const run = useRun(onError);
  const [rows, setRows] = useState<DraftRow[]>(() => day?.segments.length ? rowsOf(day.segments) : [{ in: '', out: '' }]);
  const clean = cleanRows(rows);
  const err = validate(clean);
  const c = calc(clean);
  async function save() {
    if (err) return;
    if (!clean.length && !day?.segments.length) { onClose(); return; }
    if (await run(() => journalApi.saveDay(date, clean.map(r => ({ start: r.in, end: r.out })), uid))) onClose();
  }
  async function remove() {
    if (!day?.segments.length) { onClose(); return; }
    if (!window.confirm(`Vymazať dochádzku za ${dayLabel(date)}? Aktivity zostanú zachované.`)) return;
    if (await run(() => journalApi.deleteDay(date, uid))) onClose();
  }
  return <tr className="j-hist-edit"><td colSpan={7} onClick={e => e.stopPropagation()}>
    <SegmentRows rows={rows} onChange={setRows} onCommit={() => void save()} onEscape={onClose} liveMin={null} emptyText="Žiadne úseky — pridaj aspoň jeden." />
    <button className="secondary compact j-seg-add" onClick={() => setRows([...rows, { in: '', out: '' }])}><Plus size={14} />pridať úsek</button>
    <div className="j-he-foot">
      <span className={'j-he-info' + (err ? ' warn' : '')}>{err || `Odpracované ${fmtDur(c.work)} · prestávky ${fmtDur(c.brk)} · ${c.count} ${plural(c.count, 'úsek', 'úseky', 'úsekov')}`}</span>
      <span className="j-acts">
        <button className="danger compact" onClick={() => void remove()}>Vymazať deň</button>
        <button className="secondary compact" onClick={onClose}>Zrušiť</button>
        <button className="primary compact" disabled={!!err} onClick={() => void save()}>Uložiť</button>
      </span>
    </div>
  </td></tr>;
}

/** „Čas v práci“: dlaždice, graf mesiaca a dni s úpravou (renderOverview, renderMonthChart, renderHistory). */
export function MonthOverview({ uid, month, setMonth, attDate, setAttDate, onError }: {
  uid: number | null; month: string; setMonth: (m: string) => void; attDate: string; setAttDate: (d: string) => void; onError: (m: string) => void;
}) {
  const run = useRun(onError);
  const q = useOverview(month, uid);
  const o = q.data;
  const now = baNow();
  const curMonth = now.date.slice(0, 7);
  const [edit, setEdit] = useState<string | null>(null);
  const [newDate, setNewDate] = useState(now.date);
  const mShort = MONTHS[Number(month.slice(5)) - 1];
  const days = o?.days ?? [];
  const byDate = new Map(days.map(d => [d.date, d]));
  const shownDays = days.filter(d => d.segments.length || d.date === edit).slice().reverse();
  const max = Math.max(1, ...days.map(d => Math.max(d.worked_min, d.logged_min)));
  const t = o?.totals;
  const openEdit = (d: string) => {
    if (d.slice(0, 7) !== month) setMonth(d.slice(0, 7));
    setEdit(d); setAttDate(d);
  };
  const rt = ratioInfo(t?.logged_min ?? 0, t?.worked_min ?? 0);
  return <section className="j-card">
    <div className="j-card-head"><h2>Čas v práci</h2>
      <div className="j-date-nav">
        <button className="j-mini" aria-label="Predchádzajúci mesiac" onClick={() => { setMonth(shiftMonth(month, -1)); setEdit(null); }}><ChevronLeft size={17} /></button>
        <span className="j-m-label" aria-live="polite">{monthName(month)}</span>
        <button className="j-mini" aria-label="Nasledujúci mesiac" disabled={month >= curMonth} onClick={() => { setMonth(shiftMonth(month, 1)); setEdit(null); }}><ChevronRight size={17} /></button>
        {month !== curMonth && <button className="secondary compact" onClick={() => { setMonth(curMonth); setEdit(null); }}>Tento mesiac</button>}
      </div>
    </div>
    <div className="j-summary">
      <div className="j-stat"><div className="j-k">V práci · {mShort}</div><div className="j-v">{fmtDur(t?.worked_min ?? 0)}</div>
        <div className="j-s">{t?.worked_days ? `${t.worked_days} ${plural(t.worked_days, 'deň', 'dni', 'dní')}${t.avg_closed_min != null ? ' · ø ' + fmtDur(t.avg_closed_min) + '/deň' : ''}` : 'žiadna dochádzka'}</div></div>
      <div className="j-stat total"><div className="j-k">Odpísané · {mShort}</div><div className="j-v">{fmtDur(t?.logged_min ?? 0)}</div>
        <div className="j-s">{t?.worked_min ? <>{rt.pct} % z času v práci{t.worked_min - t.logged_min >= 1 ? <> · <span className={rt.cls === 'low' ? 'j-low' : ''}>neodpísané {fmtDur(t.worked_min - t.logged_min)}</span></> : t.logged_min - t.worked_min >= 1 ? <> · <span className="j-over">o {fmtDur(t.logged_min - t.worked_min)} viac</span></> : null}</> : t?.logged_min ? 'bez dochádzky v tomto mesiaci' : 'zatiaľ nič'}</div>
        <Meter logged={t?.logged_min ?? 0} work={t?.worked_min ?? 0} /></div>
      {o?.today && <div className="j-stat"><div className="j-k">Dnes</div><div className="j-v">{fmtDur(o.today.worked_min)}</div>
        <div className="j-s"><LoggedLine logged={o.today.logged_min} work={o.today.worked_min} /></div><Meter logged={o.today.logged_min} work={o.today.worked_min} /></div>}
      {o?.week && <div className="j-stat"><div className="j-k">Tento týždeň</div><div className="j-v">{fmtDur(o.week.worked_min)}</div>
        <div className="j-s"><LoggedLine logged={o.week.logged_min} work={o.week.worked_min} /></div><Meter logged={o.week.logged_min} work={o.week.worked_min} /></div>}
    </div>
    <div className="j-month-chart" style={{ gridTemplateColumns: `repeat(${days.length || 30}, minmax(0, 1fr))` }}>
      {days.map(d => {
        const has = d.worked_min > 0, pct = Math.round(d.worked_min / max * 100), lp = has ? Math.min(100, Math.round(d.logged_min / d.worked_min * 100)) : 0;
        const title = `${DAY_NAMES[weekday(d.date)]} ${Number(d.date.slice(8))}.${Number(d.date.slice(5, 7))}.${d.holiday ? ' · ' + d.holiday : ''}: ` + (has ? `v práci ${fmtDur(d.worked_min)} · odpísané ${fmtDur(d.logged_min)} (${Math.round(d.logged_min / d.worked_min * 100)} %)` : d.logged_min ? `bez dochádzky · odpísané ${fmtDur(d.logged_min)}` : 'bez záznamu');
        return <button key={d.date} title={title} aria-label={title}
          className={'j-mday' + (has ? ' has' : '') + (d.weekend || d.holiday ? ' we' : '') + (d.date === now.date ? ' today' : '') + (d.date === attDate ? ' sel' : '')}
          onClick={() => d.segments.length ? openEdit(d.date) : setAttDate(d.date)}>
          <span className="j-bar-track"><span className="j-bar" style={{ height: (has ? Math.max(pct, 4) : 3) + '%' }}>{has && <span className={'j-bar-logged' + (d.logged_min > d.worked_min * 1.05 ? ' over' : '')} style={{ height: lp + '%' }} />}</span></span>
          <span className="j-dn">{Number(d.date.slice(8))}</span>
        </button>;
      })}
    </div>
    <div className="j-legend"><span><i className="lg-log" />odpísané v aktivitách</span><span><i className="lg-work" />v práci</span></div>
    <div className="j-hist-bar">
      <span className="j-lbl">Dni · {mShort} · klikni na deň a uprav</span>
      <span className="j-row">
        <input type="date" value={newDate} max={now.date} aria-label="Dátum na pridanie" onChange={e => setNewDate(e.target.value)} />
        <button className="secondary compact" onClick={() => newDate && openEdit(newDate)}><Plus size={14} />Pridať deň</button>
      </span>
    </div>
    <div className="table-scroll">
      <table className="j-hist">
        <thead><tr><th>Deň</th><th>Príchod</th><th>Odchod</th><th className="num">Prest.</th><th className="num">V práci</th><th className="num">Odpísané</th><th /></tr></thead>
        <tbody>
          {!shownDays.length && <tr><td colSpan={7} className="j-empty-small">{month === curMonth ? 'Tento mesiac zatiaľ žiadna dochádzka. Pípni príchod, alebo pridaj deň ručne cez „Pridať deň“.' : 'V tomto mesiaci nie je žiadna dochádzka.'}</td></tr>}
          {shownDays.map(d => {
            const ri = ratioInfo(d.logged_min, d.worked_min);
            return <Fragment key={d.date}>
              <tr className={d.date === edit ? 'sel editing' : d.date === attDate ? 'sel' : ''} title={d.segments.length ? 'Úseky: ' + d.segments.map(s => `${s.start}–${s.end ?? '…'}`).join(', ') + ' · kliknutím upravíš' : 'Kliknutím upravíš'}
                onClick={() => edit === d.date ? setEdit(null) : openEdit(d.date)}>
                <td className="j-day-cell">{dayLabel(d.date)}{d.count > 1 && <small> {d.count}×</small>}</td>
                <td>{d.first ?? <span className="dim">—</span>}</td>
                <td>{d.open ? <span className="dim">beží</span> : d.last ? <>{d.last}{d.last_next_day && <sup className="j-nd-sup" title="nasledujúci deň">+1</sup>}</> : <span className="dim">—</span>}</td>
                <td className="num">{d.break_min ? fmtShort(d.break_min) : <span className="dim">—</span>}</td>
                <td className="num">{d.worked_min ? fmtShort(d.worked_min) : <span className="dim">—</span>}</td>
                <td className="num j-lgd">{d.logged_min || d.worked_min ? <><div className="j-lgv">{d.logged_min ? fmtShort(d.logged_min) : <span className="dim">0h00</span>}{d.worked_min > 0 && <small className={ri.cls}>{ri.pct}%</small>}</div>{d.worked_min > 0 && <Meter logged={d.logged_min} work={d.worked_min} />}</> : <span className="dim">—</span>}</td>
                <td className="j-act-cell">
                  <button className="icon-button" aria-label={'Upraviť úseky ' + dayLabel(d.date)} onClick={e => { e.stopPropagation(); openEdit(d.date); }}><Pencil size={14} /></button>
                  {d.segments.length > 0 && <button className="icon-button" aria-label={'Vymazať deň ' + dayLabel(d.date)} onClick={e => { e.stopPropagation(); if (window.confirm(`Vymazať dochádzku za ${dayLabel(d.date)}? Aktivity zostanú zachované.`)) void run(() => journalApi.deleteDay(d.date, uid)); }}><Trash2 size={14} /></button>}
                </td>
              </tr>
              {d.date === edit && <HistoryEditor key={'e' + d.date} date={d.date} day={byDate.get(d.date)} uid={uid} onClose={() => setEdit(null)} onError={onError} />}
            </Fragment>;
          })}
        </tbody>
      </table>
    </div>
  </section>;
}

/** „Aktivity podľa projektu“ za zobrazený mesiac alebo celkovo (renderProjectStats). */
export function ProjectStats({ uid, month }: { uid: number | null; month: string }) {
  const [scope, setScope] = useState<'month' | 'all'>('all');
  const q = useProjectStats(scope === 'month' ? { start: month + '-01', end: monthEnd(month) } : null, uid);
  const rows = q.data ?? [];
  const max = Math.max(1, ...rows.map(r => r.minutes));
  return <section className="j-card">
    <div className="j-card-head"><h2>Aktivity podľa projektu</h2>
      <div className="j-seg-switch" role="group" aria-label="Obdobie">
        <button className={scope === 'all' ? 'on' : ''} onClick={() => setScope('all')}>Celkovo</button>
        <button className={scope === 'month' ? 'on' : ''} onClick={() => setScope('month')}>{monthName(month)}</button>
      </div></div>
    {!rows.length && <p className="j-empty">{q.isPending ? 'Načítavam…' : 'Zatiaľ žiadne zaznamenané aktivity.'}</p>}
    {rows.map(r => <div className="j-pstat" key={(r.project_id ?? 'x') + r.customer}>
      <div className="j-pstat-top"><span className="j-pstat-nm">{r.project}{r.customer && <small> · {r.customer}</small>}</span><span className="j-pstat-v">{r.minutes > 0 ? fmtDur(r.minutes) : r.count + '×'}</span></div>
      {r.minutes > 0 && <div className="j-pstat-meter"><span style={{ width: Math.max(Math.round(r.minutes / max * 100), 2) + '%' }} /></div>}
    </div>)}
  </section>;
}

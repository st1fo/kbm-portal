import { CloudOff, Coffee, LogIn, LogOut, RotateCcw, Undo2 } from 'lucide-react';
import type { JState, PunchKind } from './api';
import { DAY_NAMES, MONTHS, calc, dayLabel, fmtDur, minutesSince, toRows, two, weekday } from './calc';
import { useNow } from './hooks';
import type { QueuedPunch } from './punchQueue';

const STATUS_TEXT = { out: 'Mimo práce', in: 'V práci', break: 'Prestávka', done: 'Po práci' };

/** Veľké hodiny, stav a tlačidlá pípania (port renderNow/renderPresence). */
export function CommandBar({ state, queue, problem, onPunch, onRetry, skewMin }: {
  state: JState | undefined; queue: QueuedPunch[]; problem: '' | 'offline' | 'auth'; skewMin: number;
  onPunch: (k: PunchKind) => void; onRetry: () => void;
}) {
  const now = useNow(1000);
  const st = state?.status ?? 'out';
  const day = state?.day;
  const live = !!day && !day.done && day.segments.length > 0;
  const c = day ? calc(toRows(day.segments), live, minutesSince(day.date, now)) : null;
  const overnight = !!state && state.active_day !== now.date && st !== 'out';
  const btn = (label: string, kind: PunchKind, cls: string, Icon: typeof LogIn) =>
    <button key={kind} className={'j-punch ' + cls} disabled={!state} onClick={() => onPunch(kind)}><Icon size={18} />{label}</button>;
  return <section className="j-card j-cmdbar" data-status={st}>
    <div className="j-now">
      <div className="j-clock" aria-live="off">{two(now.h)}:{two(now.m)}:{two(now.s)}</div>
      <div className="j-now-date"><span className="j-live-dot" />{DAY_NAMES[weekday(now.date)]} {Number(now.date.slice(8))}. {MONTHS[Number(now.date.slice(5, 7)) - 1]} {now.date.slice(0, 4)}</div>
    </div>
    <div className="j-presence">
      <span className="j-status-pill"><i />{STATUS_TEXT[st]}{overnight ? ' · zmena od ' + dayLabel(state!.active_day) : ''}{c && c.count > 1 && (st === 'in' || st === 'break') ? ' · úsek ' + c.count : ''}</span>
      <div className="j-today">Dnes v práci: <strong>{fmtDur(c?.work ?? 0)}</strong></div>
    </div>
    <div className="j-controls" data-tour="punch">
      {st === 'out' && btn('Pípnuť príchod', 'in', 'go', LogIn)}
      {st === 'in' && [btn('Prestávka', 'break', 'pause', Coffee), btn('Pípnuť odchod', 'out', 'stop', LogOut)]}
      {st === 'break' && [btn('Koniec prestávky', 'in', 'go', LogIn), btn('Pípnuť odchod', 'out', 'stop', LogOut)]}
      {st === 'done' && [btn('Pípnuť znova príchod', 'in', 'go', LogIn), btn('Zrušiť odchod', 'undo', 'ghost', Undo2)]}
    </div>
    {(queue.length > 0 || skewMin >= 2) && <div className="j-cmd-notes">
      {queue.length > 0 && <span className="j-pending" role="status"><CloudOff size={15} />
        {queue.length === 1 ? '1 pípnutie čaká' : `${queue.length} pípnutia čakajú`} na odoslanie
        {problem === 'auth' ? ' · prihlásenie vypršalo, obnovte stránku' : problem === 'offline' ? ' · server je nedostupný' : ''}
        <button className="text-button" onClick={onRetry}><RotateCcw size={13} />Skúsiť teraz</button></span>}
      {skewMin >= 2 && <span className="j-warn-text">Hodiny zariadenia sa líšia od servera o {Math.round(skewMin)} min. Pípnutie používa čas zariadenia.</span>}
    </div>}
  </section>;
}

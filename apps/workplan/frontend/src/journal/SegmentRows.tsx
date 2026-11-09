import { TimeInput } from '../components/TimeInput';
import { X } from 'lucide-react';
import { useRef } from 'react';
import { fmtDur, unwrap } from './calc';

export type DraftRow = { in: string; out: string };

/**
 * Editovateľné úseky dňa (renderSegRows). Zmeny sa držia lokálne a uložia sa, až keď fokus opustí zoznam
 * alebo pri Enter; prepisovanie celého zoznamu počas písania by v Chrome kradlo fokus.
 */
export function SegmentRows({ rows, onChange, onCommit, liveMin, emptyText, onEscape }: {
  rows: DraftRow[]; onChange: (rows: DraftRow[]) => void; onCommit: (rows: DraftRow[]) => void;
  liveMin: number | null; emptyText: string; onEscape?: () => void;
}) {
  const host = useRef<HTMLDivElement>(null);
  const dirty = useRef(false);
  const u = unwrap(rows.map(r => ({ in: r.in || null, out: r.out || null })));
  const set = (i: number, f: keyof DraftRow, v: string) => { dirty.current = true; onChange(rows.map((r, j) => j === i ? { ...r, [f]: v } : r)); };
  if (!rows.length) return <div className="j-seg-empty">{emptyText}</div>;
  return <div ref={host} className="j-segs" onBlur={e => {
    if (e.relatedTarget && host.current?.contains(e.relatedTarget as Node)) return;
    if (dirty.current) { dirty.current = false; onCommit(rows); }
  }}>
    {rows.map((r, i) => {
      const gap = i > 0 && u[i - 1].out != null && u[i].in != null ? Math.max(0, u[i].in! - u[i - 1].out!) : null;
      const open = !!r.in && !r.out;
      const running = open && liveMin != null && i === rows.length - 1;
      const dur = u[i].in != null && u[i].out != null ? fmtDur(u[i].out! - u[i].in!) : running ? fmtDur(Math.max(0, liveMin! - u[i].in!)) : '—';
      return <div key={i}>
        {gap != null && <div className="j-seg-gap">prestávka {fmtDur(gap)}</div>}
        <div className={'j-seg-row' + (running ? ' open' : '')}>
          <span className="j-seg-no">Úsek {i + 1}</span>
          {(['in', 'out'] as const).map(f => <label key={f} className="j-seg-time">
            <small>{f === 'in' ? 'Príchod' : 'Odchod'}{u[i][f] != null && u[i][f]! >= 1440 && <em className="j-nd">+1 deň</em>}</small>
            <TimeInput value={r[f]} aria-label={`Úsek ${i + 1} ${f === 'in' ? 'príchod' : 'odchod'}`}
              onChange={v => set(i, f, v)}
              onKeyDown={e => {
                if (e.key === 'Enter') { e.preventDefault(); dirty.current = false; onCommit(rows); }
                else if (e.key === 'Escape' && onEscape) { dirty.current = false; onEscape(); }
              }} />
          </label>)}
          <span className={'j-seg-dur' + (running ? ' run' : '')} title={running ? 'beží' : undefined}>{dur}</span>
          <button type="button" className="icon-button" aria-label={`Odstrániť úsek ${i + 1}`} title="Odstrániť úsek"
            onClick={() => { dirty.current = false; const next = rows.filter((_, j) => j !== i); onChange(next); onCommit(next); }}><X size={16} /></button>
        </div>
      </div>;
    })}
    {liveMin != null && u.length > 0 && u[u.length - 1].out != null && <div className="j-seg-gap run">prestávka beží · {fmtDur(Math.max(0, liveMin - u[u.length - 1].out!))}</div>}
  </div>;
}

export const rowsOf = (segs: { start: string; end: string | null }[]): DraftRow[] => segs.map(s => ({ in: s.start || '', out: s.end || '' }));
export const cleanRows = (rows: DraftRow[]) => rows.filter(r => r.in || r.out).map(r => ({ in: r.in || null, out: r.out || null }));

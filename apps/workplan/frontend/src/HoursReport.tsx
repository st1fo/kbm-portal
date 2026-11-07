import { useQuery } from '@tanstack/react-query';
import { AlertTriangle, ChevronDown, Download, Loader2 } from 'lucide-react';
import { Fragment, useState } from 'react';
import { api, dateLabel, hrs } from './api';

type Day = { date: string; weekday: string; workday: boolean; plan: string; worked: number; absence: { type: string; hours: number; note?: string }[]; flag: string; note: string };
type Person = { user_id: number; name: string; daily_hours: number; fund: number; worked: number; holiday: number; absences: Record<string, number>; absence_total: number; difference: number; missing: string[]; days: Day[] };
type Report = { start: string; end: string; month_open: boolean; types: string[]; people: Person[] };

const FLAGS: Record<string, string> = { missing: 'bez záznamu – rátané ako odpracované', weekend: 'práca cez víkend', holiday_work: 'práca vo sviatok' };
const h = (x: number) => x ? hrs(x) : '–';

/** Mesačný výkaz hodín z plánu dochádzky (app/hours.py): admin vidí tím, ostatní seba. */
export function HoursReport({ month, group, isAdmin }: { month: string; group: string; isAdmin: boolean }) {
  const params = new URLSearchParams({ month, ...(isAdmin && group ? { group } : {}) });
  const q = useQuery<Report>({ queryKey: ['hours', month, isAdmin ? group : ''], queryFn: () => api('/hours?' + params) });
  const [open, setOpen] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function download() {
    setBusy(true); setError('');
    try {
      const r = await fetch('/workplan/api/hours/export?' + params);
      if (!r.ok) throw new Error('Export sa nepodaril. Obnovte stránku a skúste znova.');
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement('a'); a.href = url; a.download = `KBM-vykaz-hodin-${month}.xlsx`; a.click(); URL.revokeObjectURL(url);
    } catch (e) { setError((e as Error).message); }
    setBusy(false);
  }
  const d = q.data;
  return <section className="panel hours-panel">
    <div className="section-heading"><h2>{isAdmin ? 'Výkaz hodín' : 'Moje hodiny'}</h2>
      <button className="secondary compact" disabled={busy || !d?.people.length} onClick={() => void download()}>{busy ? <Loader2 size={15} className="spin" /> : <Download size={15} />}Stiahnuť XLSX</button></div>
    <p className="muted">Počíta sa z plánu v Dochádzke: pracovný deň = úväzok mínus absencie na časť dňa (napr. lekár 2 h).
      Pracovný deň bez záznamu sa ráta ako odpracovaný a je označený na kontrolu.{d?.month_open && ' Mesiac ešte prebieha, ďalšie dni sú podľa plánu.'}</p>
    {error && <div role="alert" className="error">{error}<button onClick={() => setError('')}>Zavrieť</button></div>}
    {q.isPending && <p className="empty"><Loader2 className="spin" size={16} /> Počítam hodiny…</p>}
    {q.isError && <p className="empty">Výkaz sa nepodarilo načítať.</p>}
    {d && (d.people.length ? <div className="table-scroll"><table className="data-table hours-table">
      <thead><tr><th>Zamestnanec</th><th className="num">Fond</th><th className="num">Odpracované</th>
        {d.types.map(t => <th key={t} className="num">{t}</th>)}<th className="num">Sviatky</th><th className="num">Rozdiel</th><th className="num">Bez záznamu</th></tr></thead>
      <tbody>{d.people.map(p => {
        const notable = p.days.filter(x => x.absence.length || x.flag === 'missing' || x.flag === 'weekend' || x.flag === 'holiday_work');
        const isOpen = open === p.user_id;
        return <Fragment key={p.user_id}>
          <tr className={isOpen ? 'open' : ''}>
            <td><button className="hours-name" aria-expanded={isOpen} onClick={() => setOpen(isOpen ? null : p.user_id)}>
              <ChevronDown size={15} />{p.name}<small>{hrs(p.daily_hours)} h/deň</small></button></td>
            <td className="num">{h(p.fund)}</td><td className="num"><b>{h(p.worked)}</b></td>
            {d.types.map(t => <td key={t} className="num">{h(p.absences[t] ?? 0)}</td>)}
            <td className="num">{h(p.holiday)}</td>
            <td className={'num' + (p.difference > 0 ? ' plus' : p.difference < 0 ? ' minus' : '')}>{p.difference > 0 ? '+' : ''}{p.difference ? hrs(p.difference) : '0'}</td>
            <td className="num">{p.missing.length ? <span className="hours-warn"><AlertTriangle size={14} />{p.missing.length} {p.missing.length === 1 ? 'deň' : p.missing.length < 5 ? 'dni' : 'dní'}</span> : '–'}</td>
          </tr>
          {isOpen && <tr className="hours-detail"><td colSpan={6 + d.types.length}>
            {notable.length ? <ul>{notable.map(x => <li key={x.date} className={x.flag === 'missing' ? 'warn' : ''}>
              <span className="hd-date">{x.weekday} {dateLabel(x.date)}</span>
              <span>{x.plan || 'bez plánu'}{x.worked ? ` · ${hrs(x.worked)} h práce` : ''}</span>
              {x.absence.map((a, i) => <span key={i} className="hd-abs">{a.type} {hrs(a.hours)} h{a.note ? ` (${a.note})` : ''}</span>)}
              {FLAGS[x.flag] && <span className="hd-flag">{FLAGS[x.flag]}</span>}
            </li>)}</ul> : <p className="muted">Celý mesiac podľa plánu, bez absencií.</p>}
          </td></tr>}
        </Fragment>;
      })}</tbody>
    </table></div> : <p className="empty">Žiadni zamestnanci.</p>)}
  </section>;
}

/**
 * Klientske výpočty Denníka: iba živý čas bežiaceho úseku a náhľad pri ručnej úprave.
 * Súčty v prehľadoch počíta server (app/journal_calc.py); toto je ich verný port z pôvodného denníka.
 */
import type { Segment } from './api';

export const TZ = 'Europe/Bratislava';
export const MAX_SHIFT_MIN = 24 * 60;
export const DAY_NAMES = ['Po', 'Ut', 'St', 'Št', 'Pi', 'So', 'Ne'];
export const MONTHS = ['január', 'február', 'marec', 'apríl', 'máj', 'jún', 'júl', 'august', 'september', 'október', 'november', 'december'];

type Row = { in: string | null; out: string | null };
export const toRows = (segs: Segment[]): Row[] => segs.map(s => ({ in: s.start || null, out: s.end || null }));

const fmt = new Intl.DateTimeFormat('sv-SE', { timeZone: TZ, year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' });
/** Miestny čas v Europe/Bratislava bez ohľadu na pásmo zariadenia. */
export function baNow(d: Date = new Date()) {
  const p = Object.fromEntries(fmt.formatToParts(d).map(x => [x.type, x.value]));
  const h = Number(p.hour), m = Number(p.minute), s = Number(p.second);
  return { date: `${p.year}-${p.month}-${p.day}`, hm: `${p.hour}:${p.minute}`, h, m, s, min: h * 60 + m + s / 60 };
}
const dayNo = (k: string) => Date.UTC(Number(k.slice(0, 4)), Number(k.slice(5, 7)) - 1, Number(k.slice(8, 10))) / 86400000;
export const daysBetween = (a: string, b: string) => dayNo(b) - dayNo(a);
export const addDays = (k: string, n: number) => new Date((dayNo(k) + n) * 86400000).toISOString().slice(0, 10);
/** Minúty od polnoci dňa `k` podľa nástenných hodín v Bratislave. */
export const minutesSince = (k: string, now = baNow()) => daysBetween(k, now.date) * 1440 + now.min;
export const weekday = (k: string) => (new Date(dayNo(k) * 86400000).getUTCDay() + 6) % 7;

export const hmToMin = (hm: string | null) => hm ? Number(hm.slice(0, 2)) * 60 + Number(hm.slice(3, 5)) : null;
export function unwrap(rows: Row[]) {
  let prev: number | null = null;
  return rows.map(sg => {
    const o: { in: number | null; out: number | null } = { in: null, out: null };
    (['in', 'out'] as const).forEach(f => {
      let m = hmToMin(sg[f]);
      if (m == null) { o[f] = null; return; }
      if (prev != null) while (m < prev) m += 1440;
      o[f] = m; prev = m;
    });
    return o;
  });
}
export function calc(rows: Row[], live = false, nowMin = 0) {
  const out = { work: 0, brk: 0, count: 0, first: null as number | null, last: null as number | null, breakRunning: false };
  if (!rows.length) return out;
  const u = unwrap(rows);
  let prevOut: number | null = null;
  u.forEach((sg, i) => {
    if (sg.in == null) return;
    out.count++;
    if (out.first == null) out.first = sg.in;
    if (prevOut != null && sg.in > prevOut) out.brk += sg.in - prevOut;
    const end = sg.out != null ? sg.out : live && i === u.length - 1 ? Math.max(nowMin, sg.in) : null;
    if (end != null) { out.work += end - sg.in; out.last = end; prevOut = end; } else prevOut = null;
  });
  const ls = u[u.length - 1];
  if (live && ls.out != null) { out.breakRunning = true; if (nowMin > ls.out) out.brk += nowMin - ls.out; }
  return out;
}
export function validate(rows: Row[]) {
  for (let i = 0; i < rows.length; i++) {
    if (!rows[i].in && rows[i].out) return `Pozor: úsek ${i + 1} nemá čas príchodu.`;
    if (rows[i].in && !rows[i].out && i < rows.length - 1 && rows.slice(i + 1).some(x => x.in))
      return `Pozor: úsek ${i + 1} nemá čas odchodu (nechať prázdny môže len posledný úsek).`;
  }
  let first: number | null = null, last: number | null = null;
  unwrap(rows).forEach(x => [x.in, x.out].forEach(m => { if (m == null) return; if (first == null) first = m; last = m; }));
  if (first != null && last != null && last - first > MAX_SHIFT_MIN) return 'Pozor: od prvého príchodu po posledný odchod by to bolo viac ako 24 h — skontroluj poradie časov.';
  return '';
}

export const two = (n: number) => String(n).padStart(2, '0');
export function fmtDur(min: number) { const m = Math.max(0, Math.round(min)); return `${Math.floor(m / 60)}h ${two(m % 60)}m`; }
export function fmtShort(min: number) { const m = Math.max(0, Math.round(min)); return `${Math.floor(m / 60)}h${two(m % 60)}`; }
export const minToHm = (m: number) => `${two(Math.floor(m / 60) % 24)}:${two(Math.round(m % 60))}`;
export function plural(n: number, one: string, few: string, many: string) { return n === 1 ? one : n >= 2 && n <= 4 ? few : many; }
export function ratioInfo(logged: number, work: number) {
  if (!work) return { pct: logged ? 100 : 0, cls: '' };
  const pct = Math.round(logged / work * 100);
  return { pct, cls: pct > 105 ? 'over' : pct < 90 ? 'low' : '' };
}
export const dayLabel = (k: string) => `${DAY_NAMES[weekday(k)]} ${Number(k.slice(8))}.${Number(k.slice(5, 7))}.`;
export function prettyDate(k: string) {
  const today = baNow().date;
  const pre = k === today ? 'Dnes · ' : k === addDays(today, -1) ? 'Včera · ' : '';
  return `${pre}${DAY_NAMES[weekday(k)]} ${Number(k.slice(8))}. ${MONTHS[Number(k.slice(5, 7)) - 1]}`;
}
export const monthName = (m: string) => { const n = MONTHS[Number(m.slice(5)) - 1]; return n[0].toUpperCase() + n.slice(1) + ' ' + m.slice(0, 4); };
export const shiftMonth = (m: string, n: number) => { const d = new Date(Date.UTC(Number(m.slice(0, 4)), Number(m.slice(5)) - 1 + n, 1)); return d.toISOString().slice(0, 7); };
export const monthEnd = (m: string) => addDays(shiftMonth(m, 1) + '-01', -1);
/** Čas „od–do“ aktivity s príznakom +1 deň. */
export function spanPreview(from: string, to: string) {
  const a = hmToMin(from)!, b0 = hmToMin(to)!; const next = b0 <= a; const b = next ? b0 + 1440 : b0;
  return { next, minutes: b - a };
}

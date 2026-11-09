import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { InputHTMLAttributes, KeyboardEvent } from 'react';
import { hmToMin, minToHm, two } from '../journal/calc';

/**
 * 24-hodinový čas bez AM/PM. Píše sa voľne: „8“, „830“, „8:30“, „8.30“, „17,15“ → 08:00, 08:30, 17:15.
 * Šípky hore/dole posunú o 15 min (so Shift o 1 h), pri kliknutí sa ukáže zoznam časov po 15 min.
 * onChange dostáva iba platný čas „HH:MM“ alebo prázdny reťazec.
 */
export function parseTime(text: string): string | null {
  const t = text.trim().toLowerCase().replace(/\s*h(od)?\.?$/, '');
  if (!t) return '';
  let h: number, m: number;
  const sep = t.match(/^(\d{1,2})\s*[:.,h\s]\s*(\d{0,2})$/);
  if (sep) { h = +sep[1]; m = sep[2] ? +(sep[2].length === 1 ? sep[2] + '0' : sep[2]) : 0; }
  else if (/^\d{1,4}$/.test(t)) {
    if (t.length <= 2) { h = +t; m = 0; }
    else { h = +t.slice(0, t.length - 2); m = +t.slice(-2); }
  } else return null;
  if (h === 24 && m === 0) h = 0;
  if (h > 23 || m > 59) return null;
  return `${two(h)}:${two(m)}`;
}

const SLOTS = Array.from({ length: 96 }, (_, i) => minToHm(i * 15));
const toMin = (v: string) => hmToMin(v)!;
const fromMin = (m: number) => minToHm(((m % 1440) + 1440) % 1440);  // wraps around midnight
const nowHm = () => { const d = new Date(); return fromMin(d.getHours() * 60 + d.getMinutes()); };

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, 'value' | 'onChange' | 'type'> & { value: string; onChange: (v: string) => void };

export function TimeInput({ value, onChange, onKeyDown, onBlur, onFocus, className, placeholder = 'hh:mm', ...rest }: Props) {
  const [draft, setDraft] = useState(value);
  const [open, setOpen] = useState(false);
  const [bad, setBad] = useState(false);
  const [pos, setPos] = useState<{ left: number; top: number; up: boolean; host: HTMLElement | null } | null>(null);
  const focused = useRef(false);
  const before = useRef(value);         // hodnota pred písaním: neplatný vstup sa vráti na ňu
  const justFocused = useRef(false);   // klik do poľa označí celý čas, aby sa dal hneď prepísať
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => { if (!focused.current) { setDraft(value); setBad(false); } }, [value]);

  const show = () => {
    const r = input.current?.getBoundingClientRect(); if (!r) return;
    const up = r.bottom + 230 > window.innerHeight && r.top > 240;
    const top = up ? r.top - 4 : r.bottom + 4;
    // v modálnom dialógu (Radix) musí zoznam ležať v ňom, inak by klik mimo dialóg zavrel; dialóg má transform,
    // preto pozícia relatívne k nemu
    const host = input.current?.closest<HTMLElement>('.dialog-content') ?? null;
    if (host) { const h = host.getBoundingClientRect(); setPos({ left: r.left - h.left + host.scrollLeft, top: top - h.top + host.scrollTop, up, host }); }
    else setPos({ left: r.left, top, up, host: null });
    setOpen(true);
  };
  // zoznam drží pozíciu pod poľom (aj v rolujúcom dialógu); pri rolovaní mimo zoznamu sa zavrie
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => { if (!list.current?.contains(e.target as Node)) setOpen(false); };
    window.addEventListener('scroll', close, true); window.addEventListener('resize', close);
    return () => { window.removeEventListener('scroll', close, true); window.removeEventListener('resize', close); };
  }, [open]);
  const current = parseTime(draft) || value;
  useLayoutEffect(() => {
    if (!open || !list.current) return;
    const target = current || nowHm();
    const i = Math.max(0, SLOTS.findIndex(s => toMin(s) >= toMin(target)) - (current ? 0 : 1));
    const el = list.current.children[i] as HTMLElement | undefined;
    if (el) list.current.scrollTop = el.offsetTop - list.current.clientHeight / 2 + el.clientHeight / 2;
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  const type = (text: string) => {
    const clean = text.replace(/[^\d:.,h\s]/gi, '').slice(0, 6);
    setDraft(clean);
    const v = parseTime(clean);
    setBad(v === null && clean.length >= 4);
    if (v !== null && v !== value) onChange(v);
  };
  const commit = () => {
    const v = parseTime(draft);
    if (v === null) { setDraft(before.current); setBad(false); if (before.current !== value) onChange(before.current); return; }
    setDraft(v); setBad(false);
    if (v !== value) onChange(v);
  };
  const pick = (v: string) => { setDraft(v); setBad(false); setOpen(false); if (v !== value) onChange(v); };
  const keys = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown') {
      e.preventDefault();
      const step = (e.shiftKey ? 60 : 15) * (e.key === 'ArrowUp' ? 1 : -1);
      const base = parseTime(draft) || value;
      const next = base ? fromMin(Math.round((toMin(base) + step) / (e.shiftKey ? 1 : 15)) * (e.shiftKey ? 1 : 15)) : nowHm();
      pick(next); return;
    }
    if (e.key === 'Escape' && open) { e.preventDefault(); e.stopPropagation(); setOpen(false); return; }
    if (e.key === 'Enter') { commit(); setOpen(false); }
    if (e.key === 'Tab') setOpen(false);
    onKeyDown?.(e);
  };

  return <span className="time-input">
    <input {...rest} ref={input} type="text" inputMode="numeric" autoComplete="off" spellCheck={false} maxLength={6}
      placeholder={placeholder} value={draft} aria-invalid={bad || undefined}
      className={(className ? className + ' ' : '') + (bad ? 'invalid' : '')}
      onChange={e => type(e.target.value)}
      onFocus={e => { focused.current = true; justFocused.current = true; before.current = value; e.currentTarget.select(); onFocus?.(e); }}
      onMouseUp={e => { if (justFocused.current) { e.preventDefault(); justFocused.current = false; } }}
      onClick={() => { justFocused.current = false; if (open) setOpen(false); else show(); }}
      // zoznam zatvoriť až po presune fokusu, inak Radix vráti fokus na dialóg
      onBlur={e => { focused.current = false; window.setTimeout(() => setOpen(false)); commit(); onBlur?.(e); }}
      onKeyDown={keys} />
    {open && pos && createPortal(<div ref={list} className={'time-list' + (pos.up ? ' up' : '')} role="listbox" aria-label="Vyber čas"
      style={{ left: pos.left, top: pos.top, position: pos.host ? 'absolute' : 'fixed' }} onMouseDown={e => e.preventDefault()}>
      {SLOTS.map(s => <button type="button" tabIndex={-1} role="option" aria-selected={s === current} key={s}
        className={s === current ? 'on' : ''} onClick={e => { e.preventDefault(); e.stopPropagation(); pick(s); }}>{s}</button>)}
    </div>, pos.host ?? document.body)}
  </span>;
}

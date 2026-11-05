/**
 * Offline odolné pípanie. Čas berie klient v momente kliknutia; pípnutie sa najprv uloží do fronty
 * v localStorage a prejaví sa okamžite (optimisticky). Fronta sa odosiela postupne v poradí; pri výpadku
 * siete, 5xx alebo vypršanom prihlásení ostáva a skúša sa znova. Server je vďaka client_id idempotentný,
 * takže opakované odoslanie (napr. z dvoch kariet) nič nezduplikuje.
 */
import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import type { QueryClient } from '@tanstack/react-query';
import type { JState, PunchKind, Status } from './api';
import { baNow } from './calc';

export type QueuedPunch = { client_id: string; kind: PunchKind; at: string };
const PREFIX = 'kbm-journal-punch-queue:';
const memory = new Map<number, QueuedPunch[]>();
const listeners = new Set<() => void>();
const flushing = new Set<number>();
const EMPTY: QueuedPunch[] = [];

function load(uid: number): QueuedPunch[] {
  try {
    const v = JSON.parse(localStorage.getItem(PREFIX + uid) || '[]');
    return Array.isArray(v) ? v.filter(x => x && typeof x.client_id === 'string' && typeof x.at === 'string') : [];
  } catch { return []; }
}
function list(uid: number) {
  if (!memory.has(uid)) memory.set(uid, load(uid));
  return memory.get(uid) || EMPTY;
}
function save(uid: number, items: QueuedPunch[]) {
  memory.set(uid, items);
  try { if (items.length) localStorage.setItem(PREFIX + uid, JSON.stringify(items)); else localStorage.removeItem(PREFIX + uid); } catch { /* súkromné okno: ostane v pamäti */ }
  listeners.forEach(fn => fn());
}
if (typeof window !== 'undefined') window.addEventListener('storage', e => {
  if (e.key?.startsWith(PREFIX)) { const uid = Number(e.key.slice(PREFIX.length)); memory.set(uid, load(uid)); listeners.forEach(fn => fn()); }
});
const subscribe = (fn: () => void) => { listeners.add(fn); return () => { listeners.delete(fn); }; };
export const pendingPunches = (uid: number) => list(uid);

/** crypto.randomUUID chýba mimo HTTPS; getRandomValues funguje všade. */
function newId() {
  const b = new Uint8Array(12);
  crypto.getRandomValues(b);
  return Array.from(b, x => x.toString(16).padStart(2, '0')).join('');
}
export const PUNCH_LABEL: Record<PunchKind, string> = { in: 'príchod', break: 'prestávka', out: 'odchod', undo: 'zrušenie odchodu' };

/** Rovnaké pravidlá ako server (clockIn/closeSeg/undoOut) nad aktívnym dňom zo stavu. */
export function applyLocal(s: JState, p: QueuedPunch): JState {
  const at = baNow(new Date(p.at));
  const st = s.status;
  let date = s.day.date, done = s.day.done;
  let segs = s.day.segments.map(x => ({ ...x }));
  if (p.kind === 'in') {
    if (st === 'in') return s;
    if (st === 'out') { date = at.date; segs = []; }
    segs.push({ start: at.hm, end: null }); done = false;
  } else if (p.kind === 'break') {
    if (st !== 'in') return s;
    segs[segs.length - 1].end = at.hm;
  } else if (p.kind === 'out') {
    if (st !== 'in' && st !== 'break') return s;
    if (st === 'in') segs[segs.length - 1].end = at.hm;
    done = true;
  } else {
    if (st !== 'done') return s;
    segs[segs.length - 1].end = null; done = false;
  }
  const status: Status = !segs.length ? 'out' : done ? 'done' : segs[segs.length - 1].end ? 'break' : 'in';
  return { ...s, active_day: date, status, day: { ...s.day, date, segments: segs, done, status }, pending: (s.pending || 0) + 1 };
}

type FlushOutcome = 'done' | 'offline' | 'auth' | 'busy';
async function flush(uid: number, onState: (s: JState) => void, onError: (msg: string) => void): Promise<FlushOutcome> {
  if (flushing.has(uid)) return 'busy';
  flushing.add(uid);
  try {
    while (list(uid).length) {
      const p = list(uid)[0];
      let r: Response;
      try {
        r = await fetch('/workplan/api/journal/punch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(p) });
      } catch { return 'offline'; }
      if (r.status === 401) return 'auth';
      if (r.status === 429 || r.status >= 500) return 'offline';
      let body: { state?: JState; detail?: unknown } | null = null;
      try { body = await r.json(); } catch { /* prázdna odpoveď */ }
      save(uid, list(uid).filter(x => x.client_id !== p.client_id));
      if (r.ok) { if (!list(uid).length && body?.state) onState(body.state); }
      else onError(`Pípnutie (${PUNCH_LABEL[p.kind]} ${baNow(new Date(p.at)).hm}) sa neuložilo: ${typeof body?.detail === 'string' ? body.detail : 'neplatná požiadavka.'}`);
    }
    return 'done';
  } finally { flushing.delete(uid); }
}

export const stateKey = (uid: number | null) => ['journal', 'state', uid] as const;

export function usePunchQueue(uid: number, qc: QueryClient) {
  const queue = useSyncExternalStore(subscribe, () => list(uid));
  const [error, setError] = useState('');
  const [problem, setProblem] = useState<'' | 'offline' | 'auth'>('');
  const run = useCallback(async () => {
    const r = await flush(uid,
      s => { qc.setQueryData(stateKey(null), s); void qc.invalidateQueries({ queryKey: ['journal'], predicate: q => q.queryKey[1] !== 'state' }); },
      msg => { setError(msg); void qc.invalidateQueries({ queryKey: ['journal'] }); });
    if (r !== 'busy') setProblem(r === 'done' ? '' : r);
  }, [uid, qc]);
  useEffect(() => {
    void run();
    const online = () => void run();
    window.addEventListener('online', online);
    const t = window.setInterval(() => { if (list(uid).length) void run(); }, 15000);
    return () => { window.removeEventListener('online', online); window.clearInterval(t); };
  }, [run, uid]);
  const punch = useCallback((kind: PunchKind) => {
    const p: QueuedPunch = { client_id: newId(), kind, at: new Date().toISOString() };
    save(uid, [...list(uid), p]);
    qc.setQueryData<JState>(stateKey(null), s => s ? applyLocal(s, p) : s);
    void run();
  }, [uid, qc, run]);
  return { queue, punch, error, clearError: () => setError(''), problem, retry: run };
}

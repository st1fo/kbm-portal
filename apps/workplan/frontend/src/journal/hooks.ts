import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useCallback, useEffect, useState } from 'react';
import { journalApi } from './api';
import { applyLocal, pendingPunches, stateKey } from './punchQueue';
import { baNow } from './calc';

/** Stav príchod/odchod; pre vlastný denník sa na serverový stav prehrajú ešte neodoslané pípnutia. */
export function useJournalState(uid: number | null, selfId: number) {
  return useQuery({
    queryKey: stateKey(uid),
    queryFn: async () => {
      const s = await journalApi.state(uid);
      const fetched = { ...s, pending: 0, skew_min: Math.abs(Date.now() - Date.parse(s.server_now)) / 60000 };
      return uid ? fetched : pendingPunches(selfId).reduce(applyLocal, fetched);
    },
    refetchInterval: 60000,
  });
}
export const useDay = (date: string, uid: number | null) => useQuery({ queryKey: ['journal', 'day', uid, date], queryFn: () => journalApi.day(date, uid) });
export const useOverview = (month: string, uid: number | null) => useQuery({ queryKey: ['journal', 'overview', uid, month], queryFn: () => journalApi.overview(month, uid) });
export const useCustomers = () => useQuery({ queryKey: ['journal', 'customers'], queryFn: journalApi.customers });
export const useProjects = (all = false) => useQuery({ queryKey: ['journal', 'projects', all], queryFn: () => journalApi.projects(all) });
export const useTasks = (uid: number | null, all = false) => useQuery({ queryKey: ['journal', 'tasks', uid, all], queryFn: () => journalApi.tasks(uid, all) });
export const useRecentActivities = (uid: number | null) => useQuery({ queryKey: ['journal', 'activities', uid, 'recent'], queryFn: () => journalApi.activities({ limit: 600 }, uid) });
export const useTaskActivities = (taskId: number, uid: number | null) =>
  useQuery({ queryKey: ['journal', 'activities', uid, 'task', taskId], queryFn: () => journalApi.activities({ task_id: taskId, limit: 5000 }, uid) });
export const useTodos = (uid: number | null) => useQuery({ queryKey: ['journal', 'todos', uid], queryFn: () => journalApi.todos(uid) });
export const useProjectStats = (range: { start: string; end: string } | null, uid: number | null) =>
  useQuery({ queryKey: ['journal', 'projectStats', uid, range?.start ?? 'all'], queryFn: () => journalApi.projectStats(range, uid) });

/** Spustí zápis, obnoví dáta Denníka a vráti chybu ako text (alebo ''). */
export function useRun(onError: (msg: string) => void) {
  const qc = useQueryClient();
  return useCallback(async <T,>(fn: () => Promise<T>): Promise<T | undefined> => {
    try {
      const r = await fn();
      await qc.invalidateQueries({ queryKey: ['journal'] });
      return r;
    } catch (e) {
      onError((e as Error).message);
      void qc.invalidateQueries({ queryKey: ['journal'] });
      return undefined;
    }
  }, [qc, onError]);
}

/** Tikajúce hodiny (Europe/Bratislava). */
export function useNow(ms = 1000) {
  const [now, setNow] = useState(() => baNow());
  useEffect(() => { const t = window.setInterval(() => setNow(baNow()), ms); return () => window.clearInterval(t); }, [ms]);
  return now;
}

export function useLocalSetting(key: string, initial: string): [string, (v: string) => void] {
  const [v, setV] = useState(() => { try { return localStorage.getItem(key) || initial; } catch { return initial; } });
  return [v, (x: string) => { setV(x); try { localStorage.setItem(key, x); } catch { /* bez úložiska */ } }];
}

/** Okamžité prepnutie checkboxu; po uložení (alebo chybe) prevezme hodnotu zo servera. */
export function useOptimisticFlags() {
  const [flags, setFlags] = useState<Record<number, boolean>>({});
  const get = (id: number, server: boolean) => flags[id] ?? server;
  const toggle = async (id: number, value: boolean, save: () => Promise<unknown>) => {
    setFlags(f => ({ ...f, [id]: value }));
    try { await save(); } finally { setFlags(f => { const n = { ...f }; delete n[id]; return n; }); }
  };
  return { get, toggle };
}

import axios from 'axios';

export const api = axios.create({ baseURL: '/vehicle-logbook/api' });
export type Value = string | number | boolean | null | undefined;
export type Row = Record<string, Value> & { id?: number };
export type Entry = Row & { warnings: string[] };
export type Report = Row & { rows: Entry[] };
export type Account = { id?: number; username: string; name?: string; admin: boolean; active?: boolean; vehicle_ids?: number[] };
export type Preview = { digest: string; vehicle: Row; trips: Row[]; purchases: Row[];
  destinations: Row[]; holidays: string[]; warnings: string[]; duplicate: boolean };
export function errorText(error: unknown) {
  if (axios.isAxiosError(error) && typeof error.response?.data?.detail === 'string') {
    return error.response.data.detail;
  }
  return 'Požiadavka sa nepodarila. Skontrolujte pripojenie a skúste to znova.';
}
export function localDay() {
  return new Intl.DateTimeFormat('sv-SE', { timeZone: 'Europe/Bratislava' }).format(new Date());
}
export function fmt(value: Value, places = 2) {
  if (value == null || value === '') return '–';
  return new Intl.NumberFormat('sk-SK', { maximumFractionDigits: places }).format(Number(value));
}
export const monthName = (month: number) =>
  new Intl.DateTimeFormat('sk-SK', { month: 'long', timeZone: 'UTC' })
    .format(new Date(Date.UTC(2025, month - 1, 1)));
/** ISO date (or datetime) as „9. 11. 2026“. */
export const dateLabel = (value: unknown) => {
  const [y, m, d] = String(value ?? '').slice(0, 10).split('-');
  return d ? `${Number(d)}. ${Number(m)}. ${y}` : '—';
};
/** Slovak noun form for a count: 1 tankovanie, 2–4 tankovania, 0 or 5+ tankovaní. */
export const plural = (count: number, one: string, few: string, many: string) =>
  count === 1 ? one : count >= 2 && count <= 4 ? few : many;

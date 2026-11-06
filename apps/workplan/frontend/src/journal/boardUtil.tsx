import { DAY_NAMES, daysBetween, weekday } from './calc';

const HUES = [152, 205, 28, 280, 340, 95, 190, 45];
export const initials = (s: string) => s.split(/\s+/).filter(Boolean).map(w => w[0]).slice(0, 2).join('').toUpperCase() || '?';

export function Avatar({ id, name, small }: { id: number; name: string; small?: boolean }) {
  return <span className={'kb-avatar' + (small ? ' sm' : '')} title={name} style={{ '--h': HUES[id % HUES.length] } as React.CSSProperties}>{initials(name)}</span>;
}

/** Popis termínu: dnes / zajtra / včera / „od po 3.11.“ a trieda pre farbu. */
export function dueInfo(due: string | null, today: string) {
  if (!due) return null;
  const diff = daysBetween(today, due);
  let label = diff === 0 ? 'dnes' : diff === 1 ? 'zajtra' : diff === -1 ? 'včera' : `${DAY_NAMES[weekday(due)].toLowerCase()} ${Number(due.slice(8))}.${Number(due.slice(5, 7))}.`;
  if (diff < -1) label = 'od ' + label;
  return { label, cls: diff < 0 ? 'late' : diff === 0 ? 'today' : 'due' };
}

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { api } from './api';

type Page = { key: string; label: string };
type Access = {
  pages: Page[];
  groups: { id: number; name: string; members: number }[];
  users: { id: number; name: string; admin: boolean; group_ids: number[]; groups: string[]; effective: string[] }[];
  config: { groups: Record<string, string[]>; users: Record<string, string[]> };
};
type Draft = { groups: Record<string, string[]>; users: Record<string, string[]> };

/** Rovnaké pravidlá ako server (app/pages.py): vlastné > zjednotenie skupín; skupina bez nastavenia = všetko. */
function effective(d: Draft, pages: Page[], u: Access['users'][number]) {
  const all = pages.map(p => p.key);
  if (u.admin) return all;
  const own = d.users[u.id];
  if (own) return all.filter(k => own.includes(k));
  const g = u.group_ids.map(String);
  if (!g.length || g.some(x => !d.groups[x])) return all;
  const union = new Set(g.flatMap(x => d.groups[x]));
  return all.filter(k => union.has(k));
}

/** Administrácia: ktoré podstránky vidia skupiny a jednotliví ľudia. */
export function PageAccessPanel({ onSaved }: { onSaved: () => void }) {
  const qc = useQueryClient();
  const q = useQuery<Access>({ queryKey: ['page-access'], queryFn: () => api('/page-access') });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => { if (q.data && !draft) setDraft(structuredClone(q.data.config)); }, [q.data, draft]);
  if (q.isError) return <section className="panel full"><h2>Viditeľnosť podstránok</h2><p className="muted">Nastavenie sa nepodarilo načítať.</p></section>;
  if (!q.data || !draft) return null;
  const { pages, groups, users } = q.data;
  const all = pages.map(p => p.key);
  const dirty = JSON.stringify(draft) !== JSON.stringify(q.data.config);

  const toggle = (kind: 'groups' | 'users', id: number, key: string) => setDraft(d => {
    const cur = d![kind][id] ?? all;
    const next = cur.includes(key) ? cur.filter(k => k !== key) : all.filter(k => k === key || cur.includes(k));
    const map = { ...d![kind] };
    if (kind === 'groups' && next.length === all.length) delete map[id]; else map[id] = next;
    return { ...d!, [kind]: map };
  });
  const setOwn = (id: number, own: boolean, u: Access['users'][number]) => setDraft(d => {
    const map = { ...d!.users };
    if (own) map[id] = effective(d!, pages, u); else delete map[id];
    return { ...d!, users: map };
  });
  async function save() {
    const empty = [...Object.entries(draft!.groups), ...Object.entries(draft!.users)].some(([, v]) => !v.length);
    if (empty) { setError('Každá skupina aj človek musí vidieť aspoň jednu podstránku.'); return; }
    setBusy(true); setError('');
    try {
      const r = await api<Access>('/page-access', 'PUT', draft);
      qc.setQueryData(['page-access'], r); setDraft(structuredClone(r.config)); onSaved();
    } catch (e) { setError((e as Error).message); }
    setBusy(false);
  }
  const boxes = (kind: 'groups' | 'users', id: number, values: string[], disabled = false) => pages.map(p =>
    <td key={p.key} className="pa-cell"><input type="checkbox" checked={values.includes(p.key)} disabled={disabled}
      aria-label={`${p.label}`} onChange={() => toggle(kind, id, p.key)} /></td>);

  return <section className="panel full page-access">
    <div className="section-heading"><h2>Viditeľnosť podstránok</h2>
      <div className="pa-actions">{dirty && <button className="secondary compact" onClick={() => { setDraft(structuredClone(q.data!.config)); setError(''); }}>Zrušiť zmeny</button>}
        <button className="primary compact" disabled={!dirty || busy} onClick={() => void save()}>Uložiť</button></div></div>
    <p className="muted">Skrytú podstránku človek nevidí v menu a nemôže ju používať (napríklad bez Žiadostí nepodá žiadosť o voľno, bez Denníka nepípne).
      Skupina bez obmedzenia vidí všetko. Kto je vo viacerých skupinách, vidí všetko, čo povoľuje aspoň jedna z nich. Vlastné nastavenie človeka má prednosť pred skupinami. Administrátor vidí vždy všetko.</p>
    {error && <div role="alert" className="error">{error}<button onClick={() => setError('')}>Zavrieť</button></div>}
    <h3 className="pa-sub">Skupiny</h3>
    {groups.length ? <div className="table-scroll"><table className="data-table pa-table">
      <thead><tr><th>Skupina</th>{pages.map(p => <th key={p.key} className="pa-cell">{p.label}</th>)}</tr></thead>
      <tbody>{groups.map(g => <tr key={g.id}><td>{g.name}<small className="pa-note">{g.members} {g.members === 1 ? 'člen' : g.members < 5 ? 'členovia' : 'členov'}{!draft.groups[g.id] && ' · bez obmedzenia'}</small></td>
        {boxes('groups', g.id, draft.groups[g.id] ?? all)}</tr>)}</tbody>
    </table></div> : <p className="muted">V portáli zatiaľ nie sú skupiny. Vytvoríš ich v <a className="text-button" href="/?admin=1">správe portálu</a>.</p>}
    <h3 className="pa-sub">Ľudia</h3>
    <div className="table-scroll"><table className="data-table pa-table">
      <thead><tr><th>Meno</th><th>Nastavenie</th>{pages.map(p => <th key={p.key} className="pa-cell">{p.label}</th>)}</tr></thead>
      <tbody>{users.map(u => {
        const own = !!draft.users[u.id];
        return <tr key={u.id} className={own ? 'pa-own' : ''}><td>{u.name}<small className="pa-note">{u.admin ? 'Administrátor' : u.groups.join(', ') || 'bez skupiny'}</small></td>
          <td>{u.admin ? <span className="muted">vidí všetko</span>
            : <select aria-label={'Nastavenie pre ' + u.name} value={own ? 'own' : 'groups'} onChange={e => setOwn(u.id, e.target.value === 'own', u)}>
              <option value="groups">Podľa skupín</option><option value="own">Vlastné</option></select>}</td>
          {boxes('users', u.id, effective(draft, pages, u), u.admin || !own)}</tr>;
      })}</tbody>
    </table></div>
  </section>;
}

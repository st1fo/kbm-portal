import { useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { Dialog } from '../components/ui/dialog';
import type { ImportResult } from './api';
import { journalApi } from './api';

const LABELS: [keyof ImportResult['created'], string][] = [['days', 'Dni dochádzky'], ['segments', 'Úseky'], ['customers', 'Zákazníci'], ['projects', 'Projekty'], ['tasks', 'Úlohy'], ['activities', 'Aktivity'], ['todos', 'To-do']];

/** Import JSON zálohy zo starého Pracovného denníka: najprv náhľad (dry run), potom potvrdenie. */
export function ImportDialog({ onClose, onDone }: { onClose: () => void; onDone: (msg: string) => void }) {
  const qc = useQueryClient();
  const [payload, setPayload] = useState<unknown>(null);
  const [fileName, setFileName] = useState('');
  const [preview, setPreview] = useState<ImportResult | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  async function pick(file: File | undefined) {
    setPreview(null); setError(''); setPayload(null);
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) { setError('Súbor je väčší ako 5 MB.'); return; }
    setFileName(file.name);
    let data: unknown;
    try { data = JSON.parse(await file.text()); } catch { setError('Súbor nie je platný JSON. Vyberte zálohu z tlačidla „Exportovať zálohu (JSON)“ v starom denníku.'); return; }
    setBusy(true);
    try { setPreview(await journalApi.importLegacy(data, true)); setPayload(data); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  async function confirm() {
    setBusy(true); setError('');
    try {
      const r = await journalApi.importLegacy(payload, false);
      await qc.invalidateQueries({ queryKey: ['journal'] });
      const n = Object.values(r.created).reduce((a, b) => a + b, 0) - r.created.segments;
      onDone(n ? `Import dokončený: pribudlo ${r.created.days} dní, ${r.created.activities} aktivít, ${r.created.todos} to-do.` : 'Import dokončený: nič nové, všetko už bolo naimportované.');
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  const nothing = preview && Object.values(preview.created).every(v => v === 0);
  return <Dialog title="Import zo starého denníka" onClose={onClose}>
    <p className="muted">V starom Pracovnom denníku kliknite dole na „Exportovať zálohu (JSON)“ a súbor vyberte tu. Dáta sa priradia vám. Opakovaný import nič nezduplikuje; dni, ktoré už v Denníku máte, sa neprepíšu.</p>
    <label>Súbor zálohy<input type="file" accept="application/json,.json" onChange={e => void pick(e.target.files?.[0])} /></label>
    {busy && <p className="muted">Spracúvam {fileName}…</p>}
    {preview && <>
      <table className="data-table j-import-table"><thead><tr><th>Náhľad</th><th>Pribudne</th><th>Už existuje / preskočí sa</th></tr></thead>
        <tbody>{LABELS.map(([k, l]) => <tr key={k}><td>{l}</td><td><strong>{preview.created[k]}</strong></td><td>{preview.skipped[k]}</td></tr>)}</tbody></table>
      {preview.warnings.length > 0 && <div className="info"><strong>Upozornenia</strong><ul className="j-warn-list">{preview.warnings.map((w, i) => <li key={i}>{w}</li>)}</ul></div>}
      {nothing && <div className="info">Zo súboru nepribudne nič nové.</div>}
    </>}
    {error && <p role="alert" className="error">{error}</p>}
    <div className="dialog-actions">
      <button className="secondary" onClick={onClose}>Zrušiť</button>
      <button className="primary" disabled={!preview || busy || !!nothing} onClick={() => void confirm()}>Importovať</button>
    </div>
  </Dialog>;
}

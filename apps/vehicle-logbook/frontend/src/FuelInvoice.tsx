import { useState } from 'react';
import { Alert, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, useMediaQuery } from '@mui/material';
import { api, errorText, fmt } from './api';
import { Icon } from './icons';

type InvoiceRow = { voucher: string; day: string; clock: string; station: string; product: string; liters: number; gross: number;
  write: boolean; reason: string; warnings: string[] };
type Card = { card: string; plate: string; driver: string; vehicle: { id: number; registration: string; name: string } | null; rows: InvoiceRow[] };
type Preview = { number: string; issued: string | null; total: number; digest: string; already: boolean; imported_at: string | null;
  write: number; skip: number; cards: Card[] };
type Result = { number: string; written: number; vehicles: number;
  skipped: { plate: string; day: string; liters: number; gross: number; reason: string }[] };

const day = (iso: string) => { const [y, m, d] = iso.slice(0, 10).split('-'); return `${Number(d)}. ${Number(m)}. ${y}`; };
const plural = (n: number, one: string, few: string, many: string) => n === 1 ? one : n >= 2 && n <= 4 ? few : many;

/** Súhrnná faktúra z tankovacích kariet (FleetCor): náhľad rozdelený podľa ŠPZ, potom zápis tankovaní (iba správca). */
export function FuelInvoiceDialog({ close, done }: { close: () => void; done: (message: string) => void }) {
  const phone = useMediaQuery('(max-width:700px)');
  const [file, setFile] = useState<File>();
  const [preview, setPreview] = useState<Preview>();
  const [result, setResult] = useState<Result>();
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState('');

  async function send(apply: boolean, chosen = file) {
    if (!chosen) return;
    setBusy(true); setProblem('');
    const body = new FormData(); body.append('file', chosen);
    try {
      const { data } = await api.post('/fuel-invoices', body, { params: { apply, expected_digest: apply ? preview?.digest : undefined } });
      if (apply) setResult(data); else setPreview(data);
    } catch (error) { setProblem(errorText(error)); if (!apply) setPreview(undefined); }
    finally { setBusy(false); }
  }
  const finish = () => result ? done(`Zapísané ${result.written} ${plural(result.written, 'tankovanie', 'tankovania', 'tankovaní')} z faktúry ${result.number}.`) : close();
  const vehicles = preview ? new Set(preview.cards.filter(c => c.rows.some(r => r.write)).map(c => c.vehicle?.id)).size : 0;

  return <Dialog open onClose={busy ? undefined : finish} fullWidth maxWidth="md" fullScreen={phone}>
    <DialogTitle>Faktúra z tankovacích kariet</DialogTitle>
    <DialogContent dividers className="invoice-dialog">
      {result ? <>
        <Alert severity="success">Zapísané {result.written} {plural(result.written, 'tankovanie', 'tankovania', 'tankovaní')} do {result.vehicles} {plural(result.vehicles, 'vozidla', 'vozidiel', 'vozidiel')} z faktúry {result.number}.</Alert>
        {result.skipped.length > 0 && <Alert severity="warning" sx={{ mt: 2 }}>
          <b>Nezapísané ({result.skipped.length}):</b>
          <ul className="invoice-skipped">{result.skipped.map((s, i) => <li key={i}><b>{s.plate || 'bez ŠPZ'}</b> {day(s.day)} · {fmt(s.liters)} l · {fmt(s.gross)} € – {s.reason}</li>)}</ul>
          Tieto tankovania doplňte ručne, alebo najprv pridajte vozidlo a zapíšte ich z bločku.
        </Alert>}
      </> : <>
        <p className="muted">Nahrajte súhrnnú faktúru FleetCor (Shell Card) v PDF. Tankovania sa rozdelia k vozidlám podľa ŠPZ; pred zápisom uvidíte, čo sa zapíše a čo nie a prečo.</p>
        <div className="actions">
          <Button component="label" variant="outlined" startIcon={<Icon name="upload" />} disabled={busy}>{file ? file.name : 'Vybrať faktúru (PDF)'}
            <input hidden type="file" accept="application/pdf,.pdf" onClick={e => { e.currentTarget.value = ''; }}
              onChange={e => { const chosen = e.target.files?.[0]; setFile(chosen); setPreview(undefined); void send(false, chosen); }} />
          </Button>{busy && <CircularProgress size={20} />}
        </div>
        {problem && <Alert severity="error" sx={{ mt: 2 }}>{problem}</Alert>}
        {preview && <section className="invoice-preview">
          <h3>Faktúra {preview.number}{preview.issued && <> · {day(preview.issued)}</>} · {fmt(preview.total)} €</h3>
          {preview.already
            ? <Alert severity="error">Táto faktúra už bola zapísaná{preview.imported_at && <> ({day(preview.imported_at)})</>}. Druhýkrát sa nezapíše.</Alert>
            : <Alert severity={preview.skip ? 'warning' : 'info'}>
              Zapíše sa <b>{preview.write}</b> {plural(preview.write, 'tankovanie', 'tankovania', 'tankovaní')} do {vehicles} {plural(vehicles, 'vozidla', 'vozidiel', 'vozidiel')}.
              {preview.skip > 0 && <> Nezapíše sa <b>{preview.skip}</b> – dôvod je pri každom riadku.</>}
            </Alert>}
          {preview.cards.map(c => <div key={c.card} className={'invoice-card' + (c.vehicle ? '' : ' missing')}>
            <div className="invoice-card-head">
              <b>{c.plate || 'Bez ŠPZ'}</b>
              {c.vehicle ? <span className="muted">{c.vehicle.name}</span> : <Chip size="small" color="warning" label="nie je v Knihe jázd" />}
              {c.driver && <span className="muted">· {c.driver}</span>}
              <span className="invoice-card-sum">{fmt(c.rows.reduce((s, r) => s + Number(r.liters), 0))} l · {fmt(c.rows.reduce((s, r) => s + Number(r.gross), 0))} €</span>
            </div>
            {c.rows.map(r => <div key={r.voucher} className={'invoice-row' + (r.write ? '' : ' skip')}>
              <span className="invoice-day">{day(r.day)} <small>{r.clock}</small></span>
              <span className="invoice-station">{r.station}<small>{r.product}</small></span>
              <span className="num">{fmt(r.liters)} l</span>
              <span className="num">{fmt(r.gross)} €</span>
              <span className="invoice-state">{r.write ? <Chip size="small" color="success" variant="outlined" icon={<Icon name="check" size={14} />} label="zapíše sa" />
                : <span className="invoice-reason"><Icon name="alert" size={14} />{r.reason}</span>}
                {r.warnings.map(w => <span key={w} className="invoice-warn">{w}</span>)}</span>
            </div>)}
          </div>)}
        </section>}
      </>}
    </DialogContent>
    <DialogActions>
      {result ? <Button variant="contained" onClick={finish}>Hotovo</Button> : <>
        <Button onClick={close} disabled={busy}>Zrušiť</Button>
        <Button variant="contained" disabled={busy || !preview || preview.already || !preview.write} onClick={() => void send(true)}>
          {preview?.write ? `Zapísať ${preview.write} ${plural(preview.write, 'tankovanie', 'tankovania', 'tankovaní')}` : 'Zapísať'}</Button>
      </>}
    </DialogActions>
  </Dialog>;
}

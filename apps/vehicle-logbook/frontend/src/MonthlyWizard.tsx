import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import {
  Alert, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, MenuItem,
  FormControlLabel, Paper, Switch, Tab, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Tabs, TextField, Tooltip, useMediaQuery,
} from '@mui/material';
import { api, errorText, fmt, type Row } from './api';

type FileItem = { id:string; name:string; mime:string; size:number; status:'pending'|'done'|'error'; error:string|null; url:string };
type Receipt = { id:string; file_id:string|null; file_name:string|null; source:string; day:string|null; station:string;
  liters:string|null; gross:string|null; currency:string; vat:string|null; odo:number|null; confirmed:boolean;
  duplicate:string|null; duplicate_ok:boolean; missing:string[]; unreadable:string[] };
type Trip = { id:string; day:string; origin:string; destination:string; category:string; purpose:string; distance:number;
  start_odo:number; end_odo:number; approximate_km:number|null; day_confirmed:boolean; km_confirmed:boolean; warning:string|null };
type Message = { role:'user'|'assistant'|'error'; text:string; at:string; changed:string[] };
type Route = { destination:string; count:number; approximate_km:number|null; category:string; days:string[] };
type Draft = { id:string; status:'draft'|'saved'; version:number; month:string; error:string|null;
  start_odo:number|null; end_odo:number|null; expected_start:number; distance:number|null; planned:number;
  files:FileItem[]; receipts:Receipt[]; receipts_confirmed:boolean; receipt_problems:string[]; trips:Trip[];
  problems:string[]; stale:boolean; messages:Message[]; highlight:string[]; routes:Route[];
  can_confirm:boolean; can_plan:boolean; can_approve:boolean; origin:string; replace_existing:boolean;
  existing:{count:number; replaceable:number; manual:number; km:number};
  tank:{opening:string; purchased:string; consumed:string; closing:string; capacity:string; planned:boolean;
    rows:Record<string,{before:string; after:string}>; warnings:string[]}|null;
  assistant:{configured:boolean; model:string}; limits:{file_mb:number; files:number; message:number; types:string[]} };
type Operation = Record<string, string | number | boolean | string[] | null | undefined> & { action:string };

const receiptLabels: Record<string,string> = { day:'dátum', liters:'litre', gross:'suma', currency:'mena', station:'stanica', odo:'tachometer' };

/** Inline editable cell: saves on blur or Enter, shows the server value otherwise. */
function Cell({ value, onSave, type='text', error, label, width=110, disabled, select, children }:
  { value:string|number|null|undefined; onSave:(text:string)=>void; type?:string; error?:boolean; label:string;
    width?:number; disabled?:boolean; select?:boolean; children?:ReactNode }) {
  const current = value == null ? '' : String(value);
  const [text, setText] = useState(current);
  useEffect(() => setText(current), [current]);
  const commit = (next = text) => { if (next.trim() !== current) onSave(next.trim()); };
  return <TextField variant="standard" size="small" type={type} value={text} error={error} disabled={disabled}
    select={select} sx={{ width }} slotProps={{ htmlInput: { 'aria-label': label, step: 'any' } }}
    placeholder={error ? 'doplniť' : ''}
    onChange={event => { setText(event.target.value); if (select) commit(event.target.value); }}
    onBlur={() => { if (!select) commit(); }}
    onKeyDown={event => { if (event.key === 'Enter') (event.target as HTMLInputElement).blur(); }}>{children}</TextField>;
}

export function MonthlyWizard({ vehicle, month, exportUrl, onSaved }:
  { vehicle:Row; month:string; openingOdo:number; destinations:Row[]; onSaved:()=>void; exportUrl:(format:string)=>string }) {
  const [draft, setDraft] = useState<Draft>();
  const [problem, setProblem] = useState('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');
  const [text, setText] = useState('');
  const [files, setFiles] = useState<File[]>([]);
  const [tab, setTab] = useState<'chat'|'preview'>('chat');
  const [unseen, setUnseen] = useState(false);
  const [preview, setPreview] = useState<FileItem>();
  const [confirm, setConfirm] = useState<{ text:string; run:()=>Promise<void> }>();
  const mobile = useMediaQuery('(max-width:900px)');
  const queue = useRef(Promise.resolve());
  const chatEnd = useRef<HTMLDivElement>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const base = draft ? '/assistant/' + draft.id : '';
  const saved = draft?.status === 'saved';

  useEffect(() => {
    let active = true;
    setLoading(true);
    api.post('/vehicles/' + vehicle.id + '/assistant', { month: month + '-01' })
      .then(({ data }) => { if (active) setDraft(data); })
      .catch(error => { if (active) setProblem(errorText(error)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [vehicle.id, month]);
  useEffect(() => { chatEnd.current?.scrollIntoView({ block: 'end' }); }, [draft?.messages.length, busy]);

  function accept(data: Draft) {
    setDraft(data);
    if (data.error) setProblem(data.error);  // Gemini problems stay visible; the draft is unchanged
    if (mobile && tab === 'chat' && data.highlight.length) setUnseen(true);
  }
  /** Draft changes run one after another so the server always sees them in the user's order. */
  function run(label: string, work: () => Promise<Draft | void>) {
    const next = queue.current.then(async () => {
      setBusy(label); setProblem('');
      try { const data = await work(); if (data) accept(data); }
      catch (error) {
        setProblem(errorText(error));
        if (draft) { try { accept((await api.get('/assistant/' + draft.id)).data); } catch { /* keep current view */ } }
      } finally { setBusy(''); }
    });
    queue.current = next;
    return next;
  }
  const operate = (operations: Operation[]) =>
    run('Ukladám zmenu…', async () => (await api.post(base + '/operations', { operations })).data);

  function send(event?: FormEvent) {
    event?.preventDefault();
    if (!draft || (!text.trim() && !files.length)) return;
    const message = text.trim(), uploads = files;
    setText(''); setFiles([]);
    void run(uploads.length ? 'Nahrávam a čítam bločky…' : 'Asistent premýšľa…', async () => {
      let latest: Draft | undefined;
      for (const file of uploads) {
        const form = new FormData(); form.append('file', file);
        try { latest = (await api.post(base + '/files', form)).data; if (latest) accept(latest); }
        catch (error) { setProblem(file.name + ': ' + errorText(error)); }
      }
      if (message) return (await api.post(base + '/messages', { text: message })).data;
      if (uploads.length) return (await api.post(base + '/extract')).data;
      return latest;
    });
  }
  function pick(list: FileList | null) {
    if (!list || !draft) return;
    const limit = draft.limits.file_mb * 1_000_000;
    const chosen = Array.from(list);
    const tooBig = chosen.filter(file => file.size > limit);
    if (tooBig.length) setProblem(tooBig.map(file => file.name).join(', ') + ': súbor presahuje ' + draft.limits.file_mb + ' MB.');
    setFiles(previous => [...previous, ...chosen.filter(file => file.size <= limit)]
      .slice(0, Math.max(0, draft.limits.files - draft.files.length)));
    if (fileInput.current) fileInput.current.value = '';
  }
  function receiptEdit(receipt: Receipt, field: string, value: string) {
    if (!value) return operate([{ action: 'update_receipt', row: receipt.id, clear: [field] }]);
    const numeric = ['liters', 'gross', 'vat'].includes(field) ? value.replace(',', '.') : field === 'odo' ? Number(value) : value;
    return operate([{ action: 'update_receipt', row: receipt.id, [field]: numeric }]);
  }
  function tripEdit(trip: Trip, field: string, value: string) {
    if (field === 'km') return operate([value ? { action: 'update_trip', row: trip.id, km: Number(value) }
      : { action: 'update_trip', row: trip.id, clear: ['km'] }]);
    if (!value) return;
    return operate([{ action: 'update_trip', row: trip.id, [field]: value }]);
  }
  function odometer(field: 'start_odo'|'end_odo', value: string) {
    return operate([value ? { action: 'set_odometers', [field]: Number(value) } : { action: 'set_odometers', clear: [field] }]);
  }
  const approve = () => run('Ukladám výkaz…', async () => {
    const data = (await api.post(base + '/approve', { version: draft!.version })).data as Draft;
    if (data.status === 'saved') onSaved();
    return data;
  });
  const discard = () => run('Mažem koncept…', async () => {
    await api.delete(base);
    return (await api.post('/vehicles/' + vehicle.id + '/assistant', { month: month + '-01' })).data;
  });

  if (loading) return <div className="spinner"><CircularProgress size={30} /></div>;
  if (!draft) return <Alert severity="warning">{problem || 'Sprievodcu sa nepodarilo otvoriť.'}</Alert>;
  const lit = new Set(draft.highlight);
  const rowClass = (id: string) => lit.has(id) ? 'draft-row changed' : 'draft-row';
  const rowKey = (id: string) => id + (lit.has(id) ? '-' + draft.version : '');
  const locked = saved || Boolean(busy);
  const delta = draft.distance;

  const previewPanel = <section className="wizard-preview" aria-label="Náhľad mesiaca">
    <Paper variant="outlined" className="panel">
      <div className="summary-strip"><h2>{String(vehicle.registration)} · {draft.month}</h2>
        <div className="actions">
          {saved ? <Chip color="success" label="Výkaz uložený" /> : <Chip variant="outlined" label="Koncept – uložený priebežne" />}
        </div></div>
      <div className="odo-grid">
        <TextField size="small" type="number" label="Tachometer na začiatku (km)" disabled={locked}
          key={'s' + draft.start_odo} defaultValue={draft.start_odo ?? ''} className={lit.has('odo') ? 'changed-field' : ''}
          helperText={'Podľa evidencie ' + fmt(draft.expected_start, 0) + ' km'}
          onBlur={event => { if (event.target.value !== String(draft.start_odo ?? '')) void odometer('start_odo', event.target.value); }} />
        <TextField size="small" type="number" label="Tachometer na konci (km)" disabled={locked}
          key={'e' + draft.end_odo} defaultValue={draft.end_odo ?? ''} className={lit.has('odo') ? 'changed-field' : ''}
          helperText={draft.end_odo == null ? 'Chýba – napíšte ho asistentovi' : ' '}
          onBlur={event => { if (event.target.value !== String(draft.end_odo ?? '')) void odometer('end_odo', event.target.value); }} />
        <div className="odo-sum"><span>Mesačný rozdiel</span><strong>{delta != null ? fmt(delta, 0) + ' km' : '–'}</strong>
          {draft.trips.length > 0 && <Chip size="small" variant="outlined" color={delta === draft.planned ? 'success' : 'error'}
            label={'Jazdy spolu ' + fmt(draft.planned, 0) + ' km'} />}</div>
      </div>
      {draft.existing.count > 0 && !saved && (draft.existing.manual > 0
        ? <Alert severity="error" sx={{ mt: 1.5 }}>Mesiac už má v evidencii {draft.existing.manual} ručne zapísaných jázd.
            Sprievodca ich neprepisuje – vyberte mesiac bez jázd alebo ich upravte v Knihe jázd.</Alert>
        : <Alert severity="warning" sx={{ mt: 1.5 }} className={lit.has('existing') ? 'changed-field' : ''}>
            Mesiac už obsahuje {draft.existing.replaceable} jázd ({fmt(draft.existing.km, 0)} km) uložených skôr ako mesačný odhad.
            <div><FormControlLabel control={<Switch checked={draft.replace_existing} disabled={locked}
              onChange={event => void operate([{ action: 'set_replace', replace: event.target.checked }])} />}
              label="Nahradiť uložený odhad týmto návrhom (staré jazdy sa odstránia až pri schválení)" /></div></Alert>)}
      <p className="muted" style={{ marginTop: 10 }}>Východisko jázd: <b>{draft.origin || 'Žilina'}</b> · vzdialenosti sú orientačné, tam a späť.</p>
      {draft.tank && <div className="tank-strip" aria-label="Stav nádrže">
        <span>Nádrž na začiatku <b>{fmt(draft.tank.opening, 1)} l</b></span>
        <span className="plus">+ natankované <b>{fmt(draft.tank.purchased, 1)} l</b></span>
        {draft.tank.planned && <span className="minus">− spotreba podľa normy <b>{fmt(draft.tank.consumed, 1)} l</b></span>}
        {draft.tank.planned && <span>= na konci <b>{fmt(draft.tank.closing, 1)} l</b></span>}
        <span className="muted">kapacita {fmt(draft.tank.capacity, 0)} l</span>
      </div>}
      {draft.routes.length > 0 && <div className={'actions routes' +(lit.has('routes') ? ' changed-field' : '')}>
        <span className="muted">Opis mesiaca:</span>
        {draft.routes.map(route => <Chip key={route.destination} size="small" variant="outlined"
          label={route.destination + ' × ' + route.count + (route.approximate_km ? ' (~' + route.approximate_km + ' km)' : '')} />)}
      </div>}
    </Paper>

    <Paper variant="outlined" className="panel">
      <div className="summary-strip"><h2>1. Tankovania z bločkov</h2>
        {draft.receipts_confirmed ? <Chip color="success" size="small" label="Bločky potvrdené" />
          : <Chip color="warning" size="small" variant="outlined" label="Čaká na kontrolu" />}</div>
      <p>Údaje prečítal asistent. Skontrolujte ich podľa originálu a opravte priamo v tabuľke alebo v chate.</p>
      {draft.files.filter(file => file.status !== 'done').map(file => <Alert key={file.id} sx={{ mt: 1 }}
        severity={file.status === 'error' ? 'error' : 'info'} action={!saved && <>
          <Button size="small" disabled={locked} onClick={() => void run('Čítam bločky…', async () => (await api.post(base + '/extract')).data)}>
            {file.status === 'error' ? 'Skúsiť znova' : 'Spracovať'}</Button>
          <Button size="small" color="inherit" disabled={locked} onClick={() => void operate([{ action: 'delete_file', row: file.id }])}>Odstrániť</Button></>}>
        <Button size="small" onClick={() => setPreview(file)}>{file.name}</Button> {file.status === 'error' ? file.error : 'čaká na spracovanie'}
      </Alert>)}
      <TableContainer className="draft-table"><Table size="small">
        <TableHead><TableRow><TableCell>#</TableCell><TableCell>Doklad</TableCell><TableCell>Dátum</TableCell>
          <TableCell>Stanica</TableCell><TableCell>Litre</TableCell><TableCell>Suma s DPH</TableCell><TableCell>Mena</TableCell>
          <TableCell>Tachometer</TableCell><TableCell>Nádrž</TableCell><TableCell>Stav</TableCell><TableCell /></TableRow></TableHead>
        <TableBody>{draft.receipts.map(receipt => {
          const file = draft.files.find(item => item.id === receipt.file_id);
          const flagged = (field: string) => receipt.missing.includes(field) || receipt.unreadable.includes(field);
          return <TableRow key={rowKey(receipt.id)} className={rowClass(receipt.id)}>
            <TableCell><b>{receipt.id}</b></TableCell>
            <TableCell>{file ? <Button size="small" onClick={() => setPreview(file)}>
              {file.mime.startsWith('image/') && !file.mime.includes('hei') ? <img className="thumb" src={file.url} alt={'Bloček ' + receipt.id} /> : 'PDF'}
            </Button> : <small className="muted">ručne</small>}</TableCell>
            <TableCell><Cell label={'Dátum ' + receipt.id} type="date" width={130} value={receipt.day} error={flagged('day')} disabled={locked}
              onSave={value => void receiptEdit(receipt, 'day', value)} /></TableCell>
            <TableCell><Cell label={'Stanica ' + receipt.id} width={130} value={receipt.station} error={flagged('station')} disabled={locked}
              onSave={value => void receiptEdit(receipt, 'station', value)} /></TableCell>
            <TableCell><Cell label={'Litre ' + receipt.id} width={70} value={receipt.liters && Number(receipt.liters)} error={flagged('liters')} disabled={locked}
              onSave={value => void receiptEdit(receipt, 'liters', value)} /></TableCell>
            <TableCell><Cell label={'Suma ' + receipt.id} width={75} value={receipt.gross && Number(receipt.gross)} error={flagged('gross')} disabled={locked}
              onSave={value => void receiptEdit(receipt, 'gross', value)} /></TableCell>
            <TableCell><Cell label={'Mena ' + receipt.id} width={45} value={receipt.currency} error={flagged('currency')} disabled={locked}
              onSave={value => void receiptEdit(receipt, 'currency', value)} /></TableCell>
            <TableCell><Cell label={'Tachometer ' + receipt.id} type="number" width={85} value={receipt.odo} disabled={locked}
              error={receipt.unreadable.includes('odo')} onSave={value => void receiptEdit(receipt, 'odo', value)} />
              {receipt.odo == null && <small className="muted block">neuvedený</small>}</TableCell>
            <TableCell className="nowrap">{draft.tank?.rows[receipt.id] && draft.tank.planned
              ? <>{fmt(draft.tank.rows[receipt.id].before, 1)} → <b>{fmt(draft.tank.rows[receipt.id].after, 1)} l</b></>
              : <small className="muted">po harmonograme</small>}</TableCell>
            <TableCell><div className="chips">
              {receipt.missing.length > 0 && <Chip size="small" color="error" label={'Doplniť: ' + receipt.missing.map(f => receiptLabels[f]).join(', ')} />}
              {receipt.unreadable.filter(f => !receipt.missing.includes(f)).length > 0 && <Chip size="small" color="warning" variant="outlined"
                label={'Nečitateľné: ' + receipt.unreadable.map(f => receiptLabels[f] ?? f).join(', ')} />}
              {receipt.duplicate && !receipt.duplicate_ok && <Tooltip title={receipt.duplicate}><Chip size="small" color="warning" label="Možná duplicita" /></Tooltip>}
              {receipt.confirmed && <Chip size="small" color="success" variant="outlined" label="Potvrdené" />}
              {!receipt.confirmed && !receipt.missing.length && <Chip size="small" variant="outlined" label="Na kontrolu" />}
            </div></TableCell>
            <TableCell className="row-actions">{!saved && <>
              {receipt.duplicate && !receipt.duplicate_ok && !receipt.duplicate.includes('mesačného sprievodcu') && <Button size="small" disabled={locked}
                onClick={() => void operate([{ action: 'dismiss_duplicate', row: receipt.id }])}>Nie je duplicita</Button>}
              <IconButton size="small" aria-label={'Odstrániť ' + receipt.id} disabled={locked}
                onClick={() => void operate([{ action: 'delete_receipt', row: receipt.id }])}>✕</IconButton></>}</TableCell>
          </TableRow>;
        })}
        {!draft.receipts.length && <TableRow><TableCell colSpan={11} className="muted">
          Zatiaľ žiadne bločky. Nahrajte ich v chate alebo pridajte ručne.</TableCell></TableRow>}</TableBody>
      </Table></TableContainer>
      {draft.receipt_problems.length > 0 && !saved && <Alert severity="warning" sx={{ mt: 1 }}>
        <ul className="tight">{draft.receipt_problems.map(item => <li key={item}>{item}</li>)}</ul></Alert>}
      {!saved && <div className="actions" style={{ marginTop: 12 }}>
        <Button variant="contained" disabled={!draft.can_confirm || locked}
          onClick={() => void run('Potvrdzujem bločky…', async () => (await api.post(base + '/confirm-receipts')).data)}>Potvrdiť bločky</Button>
        <Button variant="outlined" disabled={locked} onClick={() => void operate([{ action: 'add_receipt' }])}>+ Pridať bloček ručne</Button>
      </div>}
    </Paper>

    <Paper variant="outlined" className="panel">
      <div className="summary-strip"><h2>2. Harmonogram jázd</h2>
        {!saved && draft.receipts_confirmed && <div className="actions">
          <Button variant={draft.trips.length ? 'outlined' : 'contained'} disabled={!draft.can_plan || locked}
            onClick={() => draft.trips.length
              ? setConfirm({ text: 'Vytvoriť harmonogram nanovo z opisu mesiaca? Ručné úpravy jázd sa stratia.',
                run: () => run('Pripravujem harmonogram…', async () => (await api.post(base + '/plan', { rebuild: true })).data) })
              : void run('Pripravujem harmonogram…', async () => (await api.post(base + '/plan', { rebuild: true })).data)}>
            {draft.trips.length ? 'Vytvoriť nanovo' : 'Vytvoriť harmonogram'}</Button>
          {draft.trips.length > 0 && <Button variant={draft.stale ? 'contained' : 'outlined'} disabled={!draft.can_plan || locked}
            onClick={() => void run('Prepočítavam…', async () => (await api.post(base + '/plan', { rebuild: false })).data)}>Prepočítať harmonogram</Button>}
        </div>}</div>
      {!draft.receipts_confirmed && !saved && <p>Harmonogram sa odomkne po kontrole bločkov tlačidlom <b>Potvrdiť bločky</b>.</p>}
      {draft.trips.length > 0 && <p>Kilometre a tachometre počíta systém tak, aby sedeli s mesačným rozdielom a tankovaniami.
        <span className="legend"><Chip size="small" variant="outlined" label="odhad" /> navrhnuté, <Chip size="small" color="primary" variant="outlined" label="potvrdené" /> uviedli ste vy.</span></p>}
      {draft.stale && <Alert severity="warning" sx={{ mt: 1 }}>Evidencia vozidla sa medzitým zmenila. Prepočítajte harmonogram.</Alert>}
      {!saved && draft.tank && draft.tank.warnings.length > 0 && <Alert severity="warning" sx={{ mt: 1 }}>
        <b>Kontrola nádrže</b><ul className="tight">{draft.tank.warnings.map(item => <li key={item}>{item}</li>)}</ul></Alert>}
      {draft.problems.length > 0 && <Alert severity="error" sx={{ mt: 1 }}>
        <ul className="tight">{draft.problems.map(item => <li key={item}>{item}</li>)}</ul></Alert>}
      {draft.trips.length > 0 && <TableContainer className="draft-table"><Table size="small">
        <TableHead><TableRow><TableCell>#</TableCell><TableCell>Dátum</TableCell><TableCell>Cieľ</TableCell><TableCell>Typ</TableCell>
          <TableCell>Km</TableCell><TableCell>Tachometer</TableCell><TableCell>Nádrž po</TableCell><TableCell>Pôvod</TableCell><TableCell /></TableRow></TableHead>
        <TableBody>{draft.trips.map(trip => <TableRow key={rowKey(trip.id)} className={rowClass(trip.id)}>
          <TableCell><b>{trip.id}</b></TableCell>
          <TableCell><Cell label={'Dátum ' + trip.id} type="date" width={130} value={trip.day} disabled={locked}
            onSave={value => void tripEdit(trip, 'day', value)} /></TableCell>
          <TableCell><Cell label={'Cieľ ' + trip.id} width={140} value={trip.destination} disabled={locked}
            onSave={value => void tripEdit(trip, 'destination', value)} /></TableCell>
          <TableCell><Cell label={'Typ ' + trip.id} select width={100} value={trip.category} disabled={locked}
            onSave={value => void tripEdit(trip, 'category', value)}>
            <MenuItem value="mesto">mesto</MenuItem><MenuItem value="mimo mesto">mimo mesta</MenuItem></Cell></TableCell>
          <TableCell><Cell label={'Kilometre ' + trip.id} type="number" width={70} value={trip.distance} disabled={locked}
            onSave={value => void tripEdit(trip, 'km', value)} />
            {trip.warning && <Tooltip title={trip.warning}><small className="warn"> ⚠</small></Tooltip>}</TableCell>
          <TableCell className="nowrap">{fmt(trip.start_odo, 0)} → {fmt(trip.end_odo, 0)}</TableCell>
          <TableCell className={'nowrap' + (draft.tank && Number(draft.tank.rows[trip.id]?.after) < 0 ? ' warn' : '')}>
            {draft.tank?.rows[trip.id] ? fmt(draft.tank.rows[trip.id].after, 1) + ' l' : '–'}</TableCell>
          <TableCell><div className="chips">
            <Chip size="small" variant="outlined" color={trip.day_confirmed ? 'primary' : 'default'} label={trip.day_confirmed ? 'dátum potvrdený' : 'dátum odhad'} />
            <Chip size="small" variant="outlined" color={trip.km_confirmed ? 'primary' : 'default'} label={trip.km_confirmed ? 'km potvrdené' : 'km dopočítané'} />
          </div></TableCell>
          <TableCell>{!saved && <IconButton size="small" aria-label={'Odstrániť ' + trip.id} disabled={locked}
            onClick={() => void operate([{ action: 'delete_trip', row: trip.id }])}>✕</IconButton>}</TableCell>
        </TableRow>)}</TableBody>
      </Table></TableContainer>}
      {!saved && draft.receipts_confirmed && draft.trips.length > 0 && <Button sx={{ mt: 1 }} disabled={locked}
        onClick={() => void operate([{ action: 'add_trip', destination: 'Doplňte cieľ' }])}>+ Pridať jazdu</Button>}
    </Paper>

    <Paper variant="outlined" className="panel approve-panel">
      {saved ? <>
        <Alert severity="success">Výkaz je uložený v knihe jázd: {draft.trips.length} jázd a {draft.receipts.length} tankovaní.
          Odhadované jazdy sú vo výkaze označené ako mesačný odhad.</Alert>
        <div className="actions" style={{ marginTop: 14 }}>
          <Button variant="contained" href={exportUrl('pdf')}>Stiahnuť PDF výkaz</Button>
          <Button variant="outlined" href={exportUrl('xlsx')}>Stiahnuť XLSX</Button>
          <Button color="inherit" onClick={() => setConfirm({ text: 'Zavrieť uložený koncept a začať nový? Uložené jazdy a tankovania zostanú v evidencii.', run: discard })}>Nový koncept</Button>
        </div></> : <>
        <h2>3. Schválenie</h2>
        <p>Do evidencie sa nič nezapíše, kým výkaz neschválite. Jazdy a tankovania sa uložia naraz v jednej transakcii.</p>
        <div className="actions" style={{ marginTop: 12 }}>
          <Button size="large" variant="contained" color="success" disabled={!draft.can_approve || locked}
            onClick={() => setConfirm({ text: `Uložiť ${draft.trips.length} jázd (${fmt(draft.planned, 0)} km) a ${draft.receipts.length} tankovaní do knihy jázd?${draft.replace_existing ? ` Nahradí sa ${draft.existing.replaceable} skôr uložených odhadovaných jázd.` : ''} Odhadované jazdy zostanú vo výkaze označené.`, run: approve })}>
            Schváliť a uložiť výkaz</Button>
          <Button color="error" disabled={locked} onClick={() => setConfirm({ text: 'Zahodiť celý koncept vrátane chatu a nahraných bločkov?', run: discard })}>Zahodiť koncept</Button>
        </div></>}
    </Paper>
  </section>;

  const chatPanel = <section className="wizard-chat" aria-label="Chat s asistentom">
    <div className="chat-head"><b>Asistent</b><small className="muted">Gemini · {draft.assistant.model}</small></div>
    {!draft.assistant.configured && <Alert severity="warning" className="chat-alert">Asistent Gemini nie je na serveri nastavený
      (chýba GEMINI_API_KEY). Koncept funguje ďalej – bločky, tachometre a jazdy zadajte ručne v náhľade.</Alert>}
    <div className="chat-log" aria-live="polite">
      {draft.messages.map((item, index) => <div key={index} className={'bubble ' + item.role}>
        {item.role === 'error' && <b>Asistent je nedostupný. </b>}{item.text}
        {item.changed.filter(id => /^[BJ]\d+$/.test(id)).length > 0 && <div className="bubble-tags">
          {item.changed.filter(id => /^[BJ]\d+$/.test(id)).slice(0, 12).map(id => <span key={id}>{id}</span>)}</div>}
      </div>)}
      {busy && <div className="bubble assistant pending"><CircularProgress size={14} /> {busy}</div>}
      <div ref={chatEnd} />
    </div>
    {!saved && <form className="chat-form" onSubmit={send}>
      {files.length > 0 && <div className="chips">{files.map((file, index) => <Chip key={index} size="small" label={file.name}
        onDelete={() => setFiles(previous => previous.filter((_, i) => i !== index))} />)}</div>}
      <TextField multiline minRows={2} maxRows={6} fullWidth size="small" value={text} disabled={Boolean(busy)}
        placeholder="Napr. Na začiatku mesiaca som mal 12 240 km, na konci 13 280 km. Trikrát Košice, dvakrát Poprad."
        slotProps={{ htmlInput: { maxLength: draft.limits.message, 'aria-label': 'Správa pre asistenta' } }}
        onChange={event => setText(event.target.value)}
        onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); send(); } }} />
      <div className="actions">
        <input ref={fileInput} type="file" hidden multiple accept="image/jpeg,image/png,image/webp,image/heic,image/heif,application/pdf"
          onChange={event => pick(event.target.files)} />
        <Button variant="outlined" disabled={Boolean(busy) || draft.files.length >= draft.limits.files}
          onClick={() => fileInput.current?.click()}>+ Pridať bločky</Button>
        <Button type="submit" variant="contained" disabled={Boolean(busy) || (!text.trim() && !files.length)}>Odoslať</Button>
      </div>
      <small className="muted">Nahrané bločky a text správ dostane na analýzu služba Google Gemini. Najviac {draft.limits.files} súborov
        po {draft.limits.file_mb} MB ({draft.limits.types.join(', ')}). Asistent nič neukladá – o uložení rozhodujete vy.</small>
    </form>}
  </section>;

  return <div className="wizard">
    {problem && <Alert severity="error" onClose={() => setProblem('')}>{problem}</Alert>}
    {mobile ? <>
      <Tabs value={tab} onChange={(_, value) => { setTab(value); if (value === 'preview') setUnseen(false); }} variant="fullWidth">
        <Tab value="chat" label="Chat" /><Tab value="preview" label={unseen ? 'Náhľad mesiaca •' : 'Náhľad mesiaca'} />
      </Tabs>
      {tab === 'chat' ? chatPanel : previewPanel}
    </> : <div className="wizard-split">{previewPanel}{chatPanel}</div>}
    <Dialog open={Boolean(preview)} onClose={() => setPreview(undefined)} maxWidth="md" fullWidth>
      <DialogTitle>{preview?.name}</DialogTitle>
      <DialogContent>{preview && (preview.mime === 'application/pdf'
        ? <iframe className="receipt-frame" src={preview.url} title={preview.name} />
        : preview.mime.includes('hei') ? <Alert severity="info">Formát HEIC prehliadač nezobrazí. <a href={preview.url}>Stiahnuť originál</a></Alert>
        : <img className="receipt-image" src={preview.url} alt={preview.name} />)}</DialogContent>
      <DialogActions>{preview && <Button href={preview.url} target="_blank">Otvoriť originál</Button>}<Button onClick={() => setPreview(undefined)}>Zavrieť</Button></DialogActions>
    </Dialog>
    <Dialog open={Boolean(confirm)} onClose={() => setConfirm(undefined)}>
      <DialogTitle>Potvrdenie</DialogTitle><DialogContent>{confirm?.text}</DialogContent>
      <DialogActions><Button onClick={() => setConfirm(undefined)}>Zrušiť</Button>
        <Button variant="contained" onClick={() => { const action = confirm; setConfirm(undefined); void action?.run(); }}>Potvrdiť</Button></DialogActions>
    </Dialog>
  </div>;
}

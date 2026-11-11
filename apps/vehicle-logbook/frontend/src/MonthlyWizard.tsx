import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react';
import {
  Alert, Button, Checkbox, Chip, CircularProgress, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, Menu, MenuItem,
  FormControlLabel, Paper, Switch, Tab, Tabs, TextField, Tooltip, useMediaQuery,
} from '@mui/material';
import { api, dateLabel, errorText, fmt, monthName, plural, type Row } from './api';

type FileItem = { id:string; name:string; mime:string; size:number; status:'pending'|'done'|'error'; error:string|null; url:string };
type Receipt = { id:string; file_id:string|null; day:string|null; station:string; liters:string|null; gross:string|null;
  currency:string; odo:number|null; duplicate:string|null; duplicate_ok:boolean; missing:string[]; unreadable:string[] };
type Trip = { id:string; day:string; destination:string; category:string; purpose:string; distance:number;
  start_odo:number; end_odo:number; day_confirmed:boolean; km_confirmed:boolean; warning:string|null };
type SavedTrip = { key:string; day:string; destination:string; distance:number; start_odo:number; end_odo:number };
type SavedFuel = { key:string; day:string; station:string; liters:string; gross:string; note:string };
type Message = { role:'user'|'assistant'|'error'; text:string; changed:string[] };
type Route = { destination:string; count:number; approximate_km:number|null };
type Day = { day:string; holiday:boolean; weekend:boolean; before_start:boolean; after_reading:boolean };
type Draft = { id:string; status:'draft'|'saved'; version:number; month:string; error:string|null;
  start_odo:number|null; end_odo:number|null; end_day:string|null; closing:{ day:string; odo:number|null; estimated:boolean }; days:Day[]; expected_start:number; distance:number|null; planned:number;
  files:FileItem[]; receipts:Receipt[]; receipt_problems:string[]; trips:Trip[];
  problems:string[]; stale:boolean; messages:Message[]; highlight:string[]; routes:Route[];
  can_approve:boolean; origin:string; replace_existing:boolean;
  existing:{replaceable:number; manual:number; km:number; manual_km:number};
  saved:{km:number; trips:SavedTrip[]; fuels:SavedFuel[]};
  tank:{opening:string; purchased:string; closing:string; planned:boolean; days:Record<string,string>; warnings:string[];
    rows:Record<string,{before:string; after:string; used:string|null; per100?:string|null; km?:number; window?:string}>}|null;
  assistant:{configured:boolean; model:string}; limits:{file_mb:number; files:number; message:number; types:string[]} };
type Operation = Record<string, string | number | boolean | string[] | null | undefined> & { action:string };

const receiptLabels: Record<string,string> = { day:'dátum', liters:'litre', gross:'suma', currency:'mena', station:'stanica', odo:'tachometer' };

/** Editable field in the row dialog: saves on blur or Enter, shows the server value otherwise. */
function Cell({ value, onSave, type='text', error, label, disabled, select, children, helper, onType }:
  { value:string|number|null|undefined; onSave:(text:string)=>void; type?:string; error?:boolean; label:string;
    disabled?:boolean; select?:boolean; children?:ReactNode; helper?:string; onType?:(text:string)=>void }) {
  const current = value == null ? '' : String(value);
  const [text, setText] = useState(current);
  useEffect(() => setText(current), [current]);
  const commit = (next = text) => { if (next.trim() !== current) onSave(next.trim()); };
  return <TextField size="small" fullWidth type={type} value={text} error={error} disabled={disabled} select={select}
    label={label} helperText={helper} placeholder={error ? 'doplniť' : ''}
    slotProps={{ htmlInput: { step: 'any' }, inputLabel: type === 'date' ? { shrink: true } : undefined }}
    onChange={event => { setText(event.target.value); onType?.(event.target.value); if (select) commit(event.target.value); }}
    onBlur={() => { if (!select) commit(); }}
    onKeyDown={event => { if (event.key === 'Enter') (event.target as HTMLInputElement).blur(); }}>{children}</TextField>;
}

const WEEKDAYS = ['Ne', 'Po', 'Ut', 'St', 'Št', 'Pi', 'So'];

export function MonthlyWizard({ vehicle, month, exportUrl, onSaved }:
  { vehicle:Row; month:string; onSaved:()=>void; exportUrl:(format:string)=>string }) {
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
  const [editing, setEditing] = useState<{ kind:'trip'|'receipt'; id:string }>();
  const [kmDraft, setKmDraft] = useState<string>();   // kilometre práve písané v okne jazdy (živý prepočet nádrže)
  useEffect(() => setKmDraft(undefined), [editing?.id]);
  const [adding, setAdding] = useState<{ anchor:HTMLElement; day:string }>();
  const [checked, setChecked] = useState(false);
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
  /** New row on a given day, then its editor opens. */
  const addRow = (action: 'add_trip'|'add_receipt', day: string) => run('Pridávam…', async () => {
    const before = new Set([...(draft?.trips ?? []).map(t => t.id), ...(draft?.receipts ?? []).map(r => r.id)]);
    const data = (await api.post(base + '/operations', { operations: [action === 'add_trip'
      ? { action, day, destination: 'Doplňte cieľ' } : { action, day }] })).data as Draft;
    const created = [...data.trips.map(t => t.id), ...data.receipts.map(r => r.id)].find(id => !before.has(id));
    if (created) setEditing({ kind: action === 'add_trip' ? 'trip' : 'receipt', id: created });
    return data;
  });
  const approve = () => run('Ukladám výkaz…', async () => {
    const data = (await api.post(base + '/approve', { version: draft!.version, receipts_checked: checked })).data as Draft;
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
  const rowKey = (id: string) => id + (lit.has(id) ? '-' + draft.version : '');
  const locked = saved || Boolean(busy);
  const delta = draft.distance;
  const bookTrips = saved ? [] : draft.saved.trips;

  const [year, monthNumber] = draft.month.split('-').map(Number);
  const close = draft.closing;
  const bookFuel = saved ? [] : draft.saved.fuels;
  const issues = [...draft.receipt_problems, ...draft.problems, ...(draft.tank?.warnings ?? [])];
  // spotreba jazdy podľa normy vozidla (ako stĺpec spotreby v pôvodnom Exceli)
  /** Spotreba jazdy a stav nádrže po nej a na konci dňa; počas písania kilometrov orientačný prepočet. */
  const fuelInfo = (item: Trip) => {
    const row = draft.tank?.rows[item.id], end = draft.tank?.days[item.day];
    if (!draft.tank?.planned || !row || row.used == null) return null;
    const rate = Number(item.category === 'mesto' ? vehicle.city_rate : vehicle.outside_rate);
    const typed = kmDraft != null && kmDraft.trim() !== '' && Number(kmDraft) >= 0 ? Number(kmDraft) : null;
    const change = typed == null ? 0 : typed * rate / 100 - Number(row.used);
    const value = (base: string | number | undefined) => fmt(Number(base ?? 0) - change, 1) + ' l';
    const window = row.window ? draft.tank.rows[row.window] : undefined;
    const refuel = row.window ? Number(draft.receipts.find(r => r.id === row.window)?.liters ?? draft.saved.fuels.find(f => f.key === row.window)?.liters ?? 0) : 0;
    const windowKm = window?.km != null ? window.km + (typed == null ? 0 : typed - item.distance) : null;
    return <div className={'trip-fuel' + (typed != null && typed !== item.distance ? ' typed' : '')}>
      {windowKm != null && windowKm > 0 && refuel > 0
        ? <div><span>Priemer medzi tankovaniami</span><b>{fmt(refuel * 100 / windowKm, 1)} l/100 km</b><small>{fmt(windowKm, 0)} km, {fmt(refuel, 1)} l</small></div>
        : <div><span>Priemer medzi tankovaniami</span><small>po tejto jazde už v mesiaci nie je tankovanie</small></div>}
      <div><span>Spotreba podľa normy</span><b>{fmt(Number(row.used) + change, 1)} l</b><small>{fmt(rate, 1)} l/100 km</small></div>
      <div><span>V nádrži po jazde</span><b>{value(row.after)}</b></div>
      <div><span>Na konci dňa</span><b>{value(end)}</b></div>
      {typed != null && typed !== item.distance && <small className="muted">Orientačne pri {fmt(typed, 0)} km. Po uložení sa prepočítajú aj ďalšie dni;
        kilometre ostatných jázd sa upravia tak, aby súčet sedel s tachometrom.</small>}
    </div>;
  };
  const used = (id: string) => draft.tank?.rows[id]?.per100 != null
    ? <span className="md-fuel" title="Priemerná spotreba medzi tankovaniami"> · {fmt(draft.tank.rows[id].per100, 1)} l/100 km</span> : null;
  /** Pri tankovaní: koľko km sa prešlo od predošlého tankovania a priemer na 100 km. */
  const sinceRefuel = (id: string) => { const row = draft.tank?.rows[id];
    return row?.per100 != null ? <small className="md-per100"> · {fmt(row.km, 0)} km od predošlého · <b>{fmt(row.per100, 1)} l/100 km</b></small> : null; };
  const undated = draft.receipts.filter(receipt => !receipt.day);
  const trip = editing?.kind === 'trip' ? draft.trips.find(item => item.id === editing.id) : undefined;
  const receipt = editing?.kind === 'receipt' ? draft.receipts.find(item => item.id === editing.id) : undefined;
  const receiptFile = receipt && draft.files.find(item => item.id === receipt.file_id);
  const flagged = (item: Receipt, field: string) => item.missing.includes(field) || item.unreadable.includes(field);
  const needsReview = draft.receipts.length > 0;
  const approveText = [
    `Uložiť ${draft.trips.length} ${plural(draft.trips.length, 'jazdu', 'jazdy', 'jázd')} (${fmt(draft.planned - draft.saved.km, 0)} km) a ${draft.receipts.length} ${plural(draft.receipts.length, 'tankovanie', 'tankovania', 'tankovaní')} do knihy jázd?`,
    draft.saved.km || draft.saved.fuels.length ? 'Jazdy a tankovania, ktoré už sú v knihe, zostanú bez zmeny.' : '',
    draft.replace_existing ? `Nahradia sa skôr uložené odhadované jazdy (${draft.existing.replaceable}).` : '',
    'Odhadované jazdy zostanú vo výkaze označené.'].filter(Boolean).join(' ');
  const readDay = draft.end_day ?? draft.days[draft.days.length - 1].day;
  let level = draft.tank ? Number(draft.tank.opening) : 0;

  const receiptButton = (item: Receipt) => <button key={rowKey(item.id)} type="button" disabled={locked}
    className={'md-item fuel' + (lit.has(item.id) ? ' changed' : '') + (item.missing.length || (item.duplicate && !item.duplicate_ok) ? ' bad' : '')}
    onClick={() => setEditing({ kind: 'receipt', id: item.id })}>
    <span className="md-icon" aria-hidden>⛽</span>
    <span className="md-main">{item.liters ? fmt(item.liters, 2) + ' l' : 'litre ?'} · {item.gross ? fmt(item.gross, 2) + ' €' : 'suma ?'}
      {item.station && <small> · {item.station}</small>}{sinceRefuel(item.id)}</span>
    <span className="md-tags"><span className="md-tag">bloček {item.id}</span>
      {item.missing.length > 0 && <span className="md-tag bad">doplniť</span>}
      {item.duplicate && !item.duplicate_ok && <span className="md-tag bad">možná duplicita</span>}</span>
  </button>;

  const previewPanel = <section className="wizard-preview" aria-label="Náhľad mesiaca">
    <Paper variant="outlined" className="panel">
      <div className="summary-strip"><h2>{String(vehicle.registration)} · {monthName(monthNumber)} {year}</h2>
        {saved ? <Chip color="success" label="Výkaz uložený" /> : <Chip variant="outlined" label="Koncept – ukladá sa priebežne" />}</div>
      <div className="odo-grid">
        <TextField size="small" type="number" label="Tachometer na začiatku (km)" disabled={locked}
          key={'s' + draft.start_odo} defaultValue={draft.start_odo ?? ''} className={lit.has('odo') ? 'changed-field' : ''}
          helperText={'Podľa knihy jázd ' + fmt(draft.expected_start, 0) + ' km'}
          onBlur={event => { if (event.target.value !== String(draft.start_odo ?? '')) void odometer('start_odo', event.target.value); }} />
        <TextField size="small" type="number" label="Tachometer na konci (km)" disabled={locked}
          key={'e' + draft.end_odo} defaultValue={draft.end_odo ?? ''} className={lit.has('odo') ? 'changed-field' : ''}
          helperText={draft.end_odo == null ? 'Doplňte alebo napíšte asistentovi' : close.estimated
            ? `Stav k ${dateLabel(close.day)} odhadnutý na ${fmt(close.odo, 0)} km` : ' '}
          onBlur={event => { if (event.target.value !== String(draft.end_odo ?? '')) void odometer('end_odo', event.target.value); }} />
        <TextField size="small" type="date" label="Odčítané dňa" disabled={locked}
          key={'d' + (draft.end_day ?? '')} defaultValue={readDay}
          className={lit.has('odo') ? 'changed-field' : ''} slotProps={{ inputLabel: { shrink: true } }}
          helperText={draft.end_day ? (close.estimated ? 'po konci mesiaca – zvyšok ide do ďalšieho' : 'jazdy sa navrhnú do tohto dňa') : 'posledný deň mesiaca'}
          onBlur={event => { const value = event.target.value; if (value && value !== readDay)
            void operate([{ action: 'set_odometers', end_day: value }]); }} />
      </div>
      <div className="month-sum">
        <span>Najazdené za mesiac <b>{delta != null ? fmt(delta, 0) + ' km' : '–'}</b></span>
        {(draft.trips.length > 0 || bookTrips.length > 0) && <Chip size="small" variant="outlined" color={delta === draft.planned ? 'success' : 'error'}
          label={'v jazdách ' + fmt(draft.planned, 0) + ' km'} />}
        {draft.tank && <span className="muted">Nádrž {fmt(draft.tank.opening, 1)} l
          {draft.tank.planned ? ` → ${fmt(draft.tank.closing, 1)} l` : ''} · natankované {fmt(draft.tank.purchased, 1)} l</span>}
      </div>
      {!saved && (draft.saved.fuels.length > 0 || draft.existing.manual > 0) && <Alert severity="info" sx={{ mt: 1.5 }}>
        Načítané z knihy jázd:
        {draft.saved.fuels.length > 0 && <> {draft.saved.fuels.length} {plural(draft.saved.fuels.length, 'tankovanie', 'tankovania', 'tankovaní')} ({fmt(draft.saved.fuels.reduce((sum, f) => sum + Number(f.liters), 0), 1)} l)</>}
        {draft.saved.fuels.length > 0 && draft.existing.manual > 0 && ' a'}
        {draft.existing.manual > 0 && <> {draft.existing.manual} {plural(draft.existing.manual, 'ručne zapísaná jazda', 'ručne zapísané jazdy', 'ručne zapísaných jázd')} ({fmt(draft.existing.manual_km, 0)} km)</>}.
        Sú označené „v knihe“ a nemenia sa; návrh dopočíta iba zvyšné kilometre.</Alert>}
      {draft.existing.replaceable > 0 && !saved && <Alert severity="warning" sx={{ mt: 1.5 }} className={lit.has('existing') ? 'changed-field' : ''}>
        Mesiac už obsahuje jazdy uložené skôr ako mesačný odhad: {draft.existing.replaceable} ({fmt(draft.existing.km, 0)} km).
        <div><FormControlLabel control={<Switch checked={draft.replace_existing} disabled={locked}
          onChange={event => void operate([{ action: 'set_replace', replace: event.target.checked }])} />}
          label="Nahradiť uložený odhad týmto návrhom (staré jazdy sa odstránia až pri schválení)" /></div></Alert>}
      {draft.routes.length > 0 && <div className={'actions routes' + (lit.has('routes') ? ' changed-field' : '')}>
        <span className="muted">Opis mesiaca:</span>
        {draft.routes.map(route => <Chip key={route.destination} size="small" variant="outlined"
          label={route.destination + ' × ' + route.count + (route.approximate_km ? ' (~' + route.approximate_km + ' km)' : '')} />)}
        <span className="muted">· východisko {draft.origin || 'Žilina'}</span>
      </div>}
    </Paper>

    <Paper variant="outlined" className="panel">
      <div className="summary-strip"><h2>Mesiac po dňoch</h2>
        {!saved && draft.trips.length > 0 && <div className="actions">
          {draft.stale && <Button size="small" variant="contained" disabled={locked}
            onClick={() => void run('Prepočítavam…', async () => (await api.post(base + '/plan', { rebuild: false })).data)}>Prepočítať</Button>}
          <Button size="small" disabled={locked} onClick={() => setConfirm({ text: 'Navrhnúť jazdy znova z opisu mesiaca? Vaše úpravy jázd sa stratia.',
            run: () => run('Pripravujem návrh…', async () => (await api.post(base + '/plan', { rebuild: true })).data) })}>Navrhnúť znova</Button>
        </div>}</div>
      {!draft.trips.length && !saved && <p className="muted">Jazdy sa navrhnú samé, keď asistent pozná stav tachometra na konci a kde ste
        približne jazdili. Kliknutím na „+“ pri dni pridáte jazdu alebo tankovanie ručne.</p>}
      {draft.trips.length > 0 && <p className="muted md-legend"><span className="md-tag">odhad</span> navrhol systém ·
        <span className="md-tag sure">potvrdené</span> uviedli ste vy · <span className="md-tag book">v knihe</span> už uložené, nemení sa ·
        kliknutím na jazdu alebo bloček ho upravíte</p>}
      {draft.files.filter(file => file.status !== 'done').map(file => <Alert key={file.id} sx={{ mt: 1 }}
        severity={file.status === 'error' ? 'error' : 'info'} action={!saved && <>
          <Button size="small" disabled={locked} onClick={() => void run('Čítam bločky…', async () => (await api.post(base + '/extract')).data)}>
            {file.status === 'error' ? 'Skúsiť znova' : 'Spracovať'}</Button>
          <Button size="small" color="inherit" disabled={locked} onClick={() => void operate([{ action: 'delete_file', row: file.id }])}>Odstrániť</Button></>}>
        <Button size="small" onClick={() => setPreview(file)}>{file.name}</Button> {file.status === 'error' ? file.error : 'čaká na spracovanie'}
      </Alert>)}
      {!saved && issues.length > 0 && <Alert severity="warning" sx={{ mt: 1 }}><b>Treba skontrolovať</b>
        <ul className="tight">{issues.map(item => <li key={item}>{item}</li>)}</ul></Alert>}
      {undated.length > 0 && <div className="md-undated"><b>Bločky bez dátumu:</b> {undated.map(receiptButton)}</div>}
      {draft.tank?.planned && <div className="md-head" aria-hidden><span>Deň</span><span>Jazdy a tankovania</span><span>Nádrž večer</span><span/></div>}
      <ol className="month-days">{draft.days.map(info => {
        const date = new Date(info.day + 'T12:00:00');
        const fuels = draft.receipts.filter(item => item.day === info.day);
        const books = bookFuel.filter(item => item.day.slice(0, 10) === info.day);
        const rows = [...draft.trips.filter(item => item.day === info.day).map(item => ({ odo: item.start_odo, item, book: undefined as SavedTrip | undefined })),
          ...bookTrips.filter(item => item.day.slice(0, 10) === info.day).map(item => ({ odo: item.start_odo, item: undefined as Trip | undefined, book: item }))]
          .sort((a, b) => a.odo - b.odo);
        if (draft.tank?.days[info.day] != null) level = Number(draft.tank.days[info.day]);
        const empty = !fuels.length && !books.length && !rows.length;
        const off = info.weekend || info.holiday;
        const note = info.before_start ? 'pred začiatkom evidencie' : info.after_reading ? 'po odčítaní tachometra'
          : info.holiday ? 'sviatok' : info.weekend ? 'víkend' : '';
        return <li key={info.day} className={'md-day' + (off ? ' off' : '') + (info.after_reading || info.before_start ? ' out' : '') + (empty ? ' empty' : '')}>
          <div className="md-date"><b>{WEEKDAYS[date.getDay()]}</b> {date.getDate()}. {date.getMonth() + 1}.</div>
          <div className="md-items">
            {rows.map(({ item, book }) => book
              ? <div key={book.key} className="md-item trip book">
                <span className="md-main"><b>{book.destination || 'jazda'}</b> · {fmt(book.distance, 0)} km{used(book.key)}
                  <small> · {fmt(book.start_odo, 0)} → {fmt(book.end_odo, 0)}</small></span>
                <span className="md-tags"><span className="md-tag book">v knihe</span></span></div>
              : item && <button key={rowKey(item.id)} type="button" disabled={locked}
                className={'md-item trip' + (lit.has(item.id) ? ' changed' : '') + (item.warning ? ' warn' : '')}
                onClick={() => setEditing({ kind: 'trip', id: item.id })}>
                <span className="md-main"><b>{item.destination}</b> · {fmt(item.distance, 0)} km{used(item.id)}
                  <small> · {fmt(item.start_odo, 0)} → {fmt(item.end_odo, 0)}</small></span>
                <span className="md-tags">
                  <span className={'md-tag' + (item.day_confirmed && item.km_confirmed ? ' sure' : '')}>
                    {item.day_confirmed && item.km_confirmed ? 'potvrdené' : item.day_confirmed ? 'deň potvrdený' : item.km_confirmed ? 'km potvrdené' : 'odhad'}</span>
                  {item.warning && <Tooltip title={item.warning}><span className="md-tag bad">⚠</span></Tooltip>}</span>
              </button>)}
            {books.map(item => <div key={item.key} className="md-item fuel book">
              <span className="md-icon" aria-hidden>⛽</span>
              <span className="md-main">{fmt(item.liters, 2)} l · {fmt(item.gross, 2)} €<small> · {item.station}</small>{sinceRefuel(item.key)}</span>
              <span className="md-tags"><Tooltip title={item.note || 'kniha jázd'}><span className="md-tag book">v knihe</span></Tooltip></span></div>)}
            {fuels.map(receiptButton)}
            {empty && <span className="md-none">{note || '–'}</span>}
          </div>
          <div className="md-tank" title="V nádrži na konci dňa (podľa normy)">{draft.tank?.planned && !info.before_start && !info.after_reading ? fmt(level, 1) + ' l' : ''}</div>
          <div className="md-add">{!saved && !info.before_start && !info.after_reading && <IconButton size="small" disabled={locked}
            aria-label={'Pridať k ' + date.getDate() + '. ' + (date.getMonth() + 1) + '.'} onClick={event => setAdding({ anchor: event.currentTarget, day: info.day })}>+</IconButton>}</div>
        </li>;
      })}</ol>
      <Menu anchorEl={adding?.anchor} open={Boolean(adding)} onClose={() => setAdding(undefined)}>
        <MenuItem onClick={() => { const day = adding!.day; setAdding(undefined); void addRow('add_trip', day); }}>Pridať jazdu</MenuItem>
        <MenuItem onClick={() => { const day = adding!.day; setAdding(undefined); void addRow('add_receipt', day); }}>Pridať tankovanie (bloček)</MenuItem>
      </Menu>
    </Paper>

    <Paper variant="outlined" className="panel approve-panel">
      {saved ? <>
        <Alert severity="success">Výkaz je uložený v knihe jázd: {draft.trips.length} {plural(draft.trips.length, 'jazda', 'jazdy', 'jázd')} a {draft.receipts.length} {plural(draft.receipts.length, 'tankovanie', 'tankovania', 'tankovaní')}.
          Odhadované jazdy sú vo výkaze označené ako mesačný odhad.</Alert>
        <div className="actions" style={{ marginTop: 14 }}>
          <Button variant="contained" href={exportUrl('pdf')}>Stiahnuť PDF výkaz</Button>
          <Button variant="outlined" href={exportUrl('xlsx')}>Stiahnuť XLSX</Button>
          <Button color="inherit" onClick={() => setConfirm({ text: 'Zavrieť uložený koncept a začať nový? Uložené jazdy a tankovania zostanú v evidencii.', run: discard })}>Nový koncept</Button>
        </div></> : <>
        <h2>Schválenie</h2>
        <p>Do knihy jázd sa nič nezapíše, kým výkaz neschválite. Jazdy a tankovania sa uložia naraz.</p>
        {needsReview && <FormControlLabel control={<Checkbox checked={checked} disabled={locked} onChange={event => setChecked(event.target.checked)} />}
          label={`Údaje z ${draft.receipts.length === 1 ? 'bločku' : 'bločkov'} (${draft.receipts.length}) som skontroloval/a podľa originálov`} />}
        <div className="actions" style={{ marginTop: 12 }}>
          <Button size="large" variant="contained" color="success" disabled={!draft.can_approve || locked || (needsReview && !checked)}
            onClick={() => setConfirm({ text: approveText, run: approve })}>
            Schváliť a uložiť výkaz</Button>
          <Button color="error" disabled={locked} onClick={() => setConfirm({ text: 'Zahodiť celý koncept vrátane chatu a nahraných bločkov?', run: discard })}>Zahodiť koncept</Button>
        </div>
        {!draft.can_approve && <small className="muted">Schváliť sa dá, keď sú jazdy navrhnuté, súčet km sedí s tachometrom a nič nie je na kontrolu.</small>}</>}
    </Paper>

    <Dialog open={Boolean(trip || receipt)} onClose={() => setEditing(undefined)} fullWidth maxWidth="xs">
      {trip && <>
        <DialogTitle>Jazda {trip.id}</DialogTitle>
        <DialogContent className="edit-dialog">
          <Cell label="Dátum" type="date" value={trip.day} disabled={locked} onSave={value => void tripEdit(trip, 'day', value)} />
          <Cell label="Cieľ" value={trip.destination} disabled={locked} onSave={value => void tripEdit(trip, 'destination', value)} />
          <Cell label="Účel" value={trip.purpose} disabled={locked} onSave={value => void tripEdit(trip, 'purpose', value)} />
          <Cell label="Typ" select value={trip.category} disabled={locked} onSave={value => void tripEdit(trip, 'category', value)}>
            <MenuItem value="mesto">mesto</MenuItem><MenuItem value="mimo mesto">mimo mesta</MenuItem></Cell>
          <Cell label="Kilometre" type="number" value={trip.distance} disabled={locked} onSave={value => { setKmDraft(undefined); void tripEdit(trip, 'km', value); }}
            onType={setKmDraft}
            helper={trip.km_confirmed ? 'potvrdené vami – vymažte, ak ich má dopočítať systém' : 'dopočítané z tachometra; zadajte, ak ich poznáte'} />
          {fuelInfo(trip)}
          <p className="muted">Tachometer {fmt(trip.start_odo, 0)} → {fmt(trip.end_odo, 0)} km · {trip.day_confirmed ? 'dátum potvrdený' : 'dátum odhadnutý'}</p>
          {trip.warning && <Alert severity="warning">{trip.warning}</Alert>}
        </DialogContent>
        <DialogActions><Button color="error" disabled={locked} onClick={() => { setEditing(undefined); void operate([{ action: 'delete_trip', row: trip.id }]); }}>Odstrániť jazdu</Button>
          <Button variant="contained" onClick={() => setEditing(undefined)}>Hotovo</Button></DialogActions>
      </>}
      {receipt && <>
        <DialogTitle>Bloček {receipt.id}</DialogTitle>
        <DialogContent className="edit-dialog">
          {receiptFile && <Button size="small" onClick={() => setPreview(receiptFile)}>Zobraziť originál ({receiptFile.name})</Button>}
          <Cell label="Dátum" type="date" value={receipt.day} error={flagged(receipt, 'day')} disabled={locked} onSave={value => void receiptEdit(receipt, 'day', value)} />
          <Cell label="Stanica" value={receipt.station} error={flagged(receipt, 'station')} disabled={locked} onSave={value => void receiptEdit(receipt, 'station', value)} />
          <Cell label="Litre" value={receipt.liters && Number(receipt.liters)} error={flagged(receipt, 'liters')} disabled={locked} onSave={value => void receiptEdit(receipt, 'liters', value)} />
          <Cell label="Suma s DPH (€)" value={receipt.gross && Number(receipt.gross)} error={flagged(receipt, 'gross')} disabled={locked} onSave={value => void receiptEdit(receipt, 'gross', value)} />
          <Cell label="Tachometer (km)" type="number" value={receipt.odo} error={receipt.unreadable.includes('odo')} disabled={locked}
            helper="iba ak je na bločku" onSave={value => void receiptEdit(receipt, 'odo', value)} />
          {receipt.currency && receipt.currency !== 'EUR' && <Cell label="Mena" value={receipt.currency} error disabled={locked} onSave={value => void receiptEdit(receipt, 'currency', value)} />}
          {receipt.missing.length > 0 && <Alert severity="error">Doplňte: {receipt.missing.map(field => receiptLabels[field]).join(', ')}</Alert>}
          {receipt.duplicate && !receipt.duplicate_ok && <Alert severity="warning" action={!receipt.duplicate.includes('mesačného sprievodcu') &&
            <Button size="small" disabled={locked} onClick={() => void operate([{ action: 'dismiss_duplicate', row: receipt.id }])}>Nie je duplicita</Button>}>
            Možná duplicita: {receipt.duplicate}</Alert>}
        </DialogContent>
        <DialogActions><Button color="error" disabled={locked} onClick={() => { setEditing(undefined); void operate([{ action: 'delete_receipt', row: receipt.id }]); }}>Odstrániť bloček</Button>
          <Button variant="contained" onClick={() => setEditing(undefined)}>Hotovo</Button></DialogActions>
      </>}
    </Dialog>
  </section>;

  const chatPanel = <section className="wizard-chat" aria-label="Chat s asistentom">
    <div className="chat-head"><b>Asistent</b><small className="muted">Gemini · {draft.assistant.model}</small></div>
    {!draft.assistant.configured && <Alert severity="warning" className="chat-alert">Asistent Gemini nie je na serveri nastavený
      (chýba GEMINI_API_KEY). Koncept funguje ďalej – bločky, tachometre a jazdy zadajte ručne v náhľade.</Alert>}
    <div className="chat-log" aria-live="polite">
      {draft.messages.map((item, index) => {
        const rows = item.changed.filter(id => /^[BJ]\d+$/.test(id)).slice(0, 12);
        return <div key={index} className={'bubble ' + item.role}>
          {item.role === 'error' && <b>Asistent je nedostupný. </b>}{item.text}
          {rows.length > 0 && <div className="bubble-tags">{rows.map(id => <span key={id}>{id}</span>)}</div>}
        </div>;
      })}
      {busy && <div className="bubble assistant pending"><CircularProgress size={14} /> {busy}</div>}
      <div ref={chatEnd} />
    </div>
    {!saved && <form className="chat-form" onSubmit={send}>
      {files.length > 0 && <div className="chips">{files.map((file, index) => <Chip key={index} size="small" label={file.name}
        onDelete={() => setFiles(previous => previous.filter((_, i) => i !== index))} />)}</div>}
      <TextField multiline minRows={2} maxRows={6} fullWidth size="small" value={text} disabled={Boolean(busy)}
        placeholder="Napr. Na konci 13 280 km, odčítané 3. 11. Trikrát Košice, dvakrát Poprad."
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

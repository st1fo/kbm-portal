import { useEffect, useState, type KeyboardEvent, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import {
  Alert, Button, Chip, CircularProgress, CssBaseline, Dialog, DialogActions, DialogContent, DialogTitle,
  IconButton, Menu, MenuItem, Paper, Popover, Snackbar, TextField, ThemeProvider, Tooltip, createTheme, useMediaQuery,
} from '@mui/material';
import { api, dateLabel, errorText, fmt, localDay, monthName, plural, type Account, type Entry, type Preview, type Report, type Row } from './api';
import { DeleteVehicleDialog, destinationFields, fuelFields, RecordDialog, tripFields, UserDialog, vehicleFields, type Edit } from './forms';
import { Icon } from './icons';
import { FuelInvoiceDialog } from './FuelInvoice';
import { MonthlyWizard } from './MonthlyWizard';
import { Tour, tourSeen, tourSteps } from './Tour';
import { nextTheme, themeLabels, useTheme, type ThemeMode, type ThemePref } from './theme';
import './style.css';

const makeTheme = (mode: ThemeMode) => { const dark = mode==='dark'; return createTheme({
  palette:{mode,primary:{main:dark?'#6cc19e':'#326c56'},secondary:{main:'#e7b45f'},error:{main:dark?'#f08178':'#b93a32'},
    background:{default:dark?'#161a19':'#f3f5f3',paper:dark?'#202523':'#fff'},text:{primary:dark?'#e6ece8':'#1f2a25',secondary:dark?'#9aa69f':'#5d6b63'},
    ...(dark?{divider:'#353d39'}:{})},
  shape:{borderRadius:10},
  typography:{fontFamily:'Inter, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif',button:{textTransform:'none',fontWeight:650}},
  components:{MuiPaper:{styleOverrides:{root:{backgroundImage:'none'}}},
    MuiButton:{defaultProps:{disableElevation:true}},
    MuiTableCell:{styleOverrides:{head:{fontWeight:700,background:dark?'#1d2220':'#f0f3f1',color:dark?'#b8c2bc':'#4c5a52'}}}},
}); };
const themes = { light: makeTheme('light'), dark: makeTheme('dark') };

/** Prepínač vzhľadu v hornom pruhu: svetlý → tmavý → automaticky (podľa systému). */
function ThemeToggle({ pref, setPref }: { pref: ThemePref; setPref: (p: ThemePref) => void }) {
  const icon = pref==='light' ? 'sun' : pref==='dark' ? 'moon' : 'monitor';
  return <Tooltip title={`Vzhľad: ${themeLabels[pref]} (kliknutím prepnete na ${themeLabels[nextTheme[pref]]})`}>
    <IconButton className="theme-toggle" aria-label={'Vzhľad: '+themeLabels[pref]} onClick={()=>setPref(nextTheme[pref])}><Icon name={icon}/></IconButton>
  </Tooltip>;
}

// context: stránka pracuje s vybraným vozidlom a mesiacom; na telefóne práve tieto sú v spodnej lište, ostatné v ponuke účtu.
type NavItem = { url: string; label: string; icon: string; short?: string; admin?: boolean; context?: boolean };
const NAV: NavItem[] = [
  { url: '/', label: 'Prehľad', icon: 'home', context: true },
  { url: '/jazdy', label: 'Jazdy', icon: 'route', context: true },
  { url: '/tankovanie', label: 'Tankovanie', icon: 'fuel', context: true },
  { url: '/mesacny-navrh', label: 'Vyplniť mesiac', short: 'Mesiac', icon: 'wand', context: true },
  { url: '/vykazy', label: 'Výkazy', icon: 'report', context: true },
  { url: '/vozidla', label: 'Vozidlá', icon: 'car' },
  { url: '/sprava', label: 'Správa', icon: 'settings', admin: true },
];
const metricLabels: Record<string,[string,string]> = {
  distance:['Najazdené','km'], city_km:['V meste','km'], outside_km:['Mimo mesta','km'],
  trip_count:['Počet jázd',''], opening_odo:['Tachometer na začiatku','km'], closing_odo:['Tachometer na konci','km'],
  purchased:['Natankované','l'], calculated:['Spotreba podľa jázd','l'], normative:['Spotreba podľa normy','l'],
  consumption:['Skutočná spotreba','l'], average:['Priemerná spotreba','l/100 km'], gross:['Za palivo s DPH','€'],
  purchase_net:['Za palivo bez DPH','€'], opening_liters:['V nádrži na začiatku','l'], closing_liters:['V nádrži na konci','l'],
  correction:['Korekcia nádrže','l'], gross_per_liter:['Priemerná cena','€/l'],
  opening_net:['Hodnota nádrže na začiatku','€'], closing_net:['Hodnota nádrže na konci','€'],
  consumption_net:['Hodnota spotreby bez DPH','€'],
};
const METRIC_GROUPS: [string,string,string[]][] = [
  ['Kilometre','route',['distance','city_km','outside_km','trip_count','opening_odo','closing_odo']],
  ['Palivo','fuel',['opening_liters','purchased','consumption','closing_liters','average','calculated','normative','correction']],
  ['Peniaze','money',['gross','purchase_net','gross_per_liter','opening_net','consumption_net','closing_net']],
];
const weekday = new Intl.DateTimeFormat('sk-SK',{weekday:'short',timeZone:'UTC'});
function dayLabel(value: unknown) {
  const text = String(value ?? ''); if (!/^\d{4}-\d{2}-\d{2}/.test(text)) return text;
  const date = new Date(text.slice(0,10)+'T12:00:00Z');
  return `${date.getUTCDate()}. ${date.getUTCMonth()+1}.`+' '+weekday.format(date);
}
function shiftMonth(year:number, month:number, by:number) {
  const index = year*12 + month - 1 + by; return { year: Math.floor(index/12), month: index%12 + 1 };
}
const monthKey = (year:number, month:number) => `${year}-${String(month).padStart(2,'0')}`;

function PageTitle({ title, children }:{title:string;children?:ReactNode}) {
  return <header className="page-title"><h1>{title}</h1>{children&&<div className="actions">{children}</div>}</header>;
}
function Empty({ icon='car', title, children }:{icon?:string;title:string;children?:ReactNode}) {
  return <Paper variant="outlined" className="empty"><span className="empty-icon"><Icon name={icon} size={26}/></span>
    <h2>{title}</h2>{children}</Paper>;
}
function MetricCard({ label, value, unit, sub, icon, children }:{label:string;value:ReactNode;unit?:string;sub?:ReactNode;icon:string;children?:ReactNode}) {
  return <Paper variant="outlined" className="stat"><span className="stat-icon"><Icon name={icon}/></span>
    <span className="stat-label">{label}</span><strong>{value}{unit&&<small> {unit}</small>}</strong>
    {sub&&<span className="stat-sub">{sub}</span>}{children}</Paper>;
}

/** Výber mesiaca: šípky a po kliknutí mriežka mesiacov s rokom. */
function MonthPicker({ year, month, onChange, max }:{year:number;month:number;onChange:(y:number,m:number)=>void;max?:string}) {
  const [anchor,setAnchor] = useState<HTMLElement|null>(null);
  const [shown,setShown] = useState(year);
  const allowed = (y:number,m:number) => !max || monthKey(y,m) <= max;
  const go = (by:number) => { const n = shiftMonth(year,month,by); if (allowed(n.year,n.month)) onChange(n.year,n.month); };
  const next = shiftMonth(year,month,1);
  return <div className="month-picker">
    <IconButton size="small" aria-label="Predchádzajúci mesiac" onClick={()=>go(-1)}><Icon name="left"/></IconButton>
    <button type="button" className="month-label" onClick={e=>{setShown(year);setAnchor(e.currentTarget);}} aria-label="Vybrať mesiac">
      {monthName(month)} {year}<Icon name="down" size={15}/></button>
    <IconButton size="small" aria-label="Nasledujúci mesiac" disabled={!allowed(next.year,next.month)} onClick={()=>go(1)}><Icon name="right"/></IconButton>
    <Popover open={Boolean(anchor)} anchorEl={anchor} onClose={()=>setAnchor(null)}
      anchorOrigin={{vertical:'bottom',horizontal:'center'}} transformOrigin={{vertical:'top',horizontal:'center'}}>
      <div className="month-pop">
        <div className="month-pop-year">
          <IconButton size="small" aria-label="Predchádzajúci rok" onClick={()=>setShown(shown-1)}><Icon name="left"/></IconButton>
          <b>{shown}</b>
          <IconButton size="small" aria-label="Nasledujúci rok" onClick={()=>setShown(shown+1)}><Icon name="right"/></IconButton>
        </div>
        <div className="month-pop-grid">{Array.from({length:12},(_,i)=>i+1).map(m=>
          <button type="button" key={m} disabled={!allowed(shown,m)} className={shown===year&&m===month?'on':''}
            onClick={()=>{onChange(shown,m);setAnchor(null);}}>{monthName(m).slice(0,3)}</button>)}</div>
      </div>
    </Popover>
  </div>;
}

function ImportPanel({ done }: {done:(vehicleId:number)=>void}) {
  const [file,setFile]=useState<File>();
  const [preview,setPreview]=useState<Preview>();
  const [problem,setProblem]=useState('');
  const [busy,setBusy]=useState(false);
  async function upload(apply:boolean, chosen=file) {
    if (!chosen) return;
    setBusy(true);setProblem('');
    const body=new FormData(); body.append('file',chosen);
    try {
      const {data}=await api.post('/import',body,{params:{apply,expected_digest:apply?preview?.digest:undefined}});
      if (apply) {setPreview(undefined);setFile(undefined);done(data.vehicle_id);}
      else setPreview(data);
    } catch(error){setProblem(errorText(error));} finally{setBusy(false);}
  }
  return <Paper className="panel" variant="outlined"><h2><Icon name="upload"/> Import zo starého Excelu</h2>
    <p className="muted">Nahrajte zošit XLSM. Pred uložením uvidíte, čo sa naimportuje.</p>
    <div className="actions"><Button component="label" variant="outlined" disabled={busy}>{file ? file.name : 'Vybrať súbor XLSM'}
      <input hidden type="file" accept=".xlsm" onClick={e=>{e.currentTarget.value='';}}
        onChange={e=>{const chosen=e.target.files?.[0];setFile(chosen);setPreview(undefined);setProblem('');void upload(false,chosen);}} />
      </Button>{busy&&<CircularProgress size={20}/>}</div>
    {problem&&<Alert severity="error" sx={{mt:2}}>{problem}</Alert>}
    {preview&&<section className="preview">
      <h3>{String(preview.vehicle.registration)}: {preview.trips.length} jázd, {preview.purchases.length} tankovaní, {preview.destinations.length} cieľov</h3>
      {preview.duplicate&&<Alert severity="warning">Toto vozidlo alebo súbor už v evidencii je.</Alert>}
      {preview.warnings.length>0&&<details><summary>Upozornenia ({preview.warnings.length})</summary>
        <ul>{preview.warnings.map((warning,index)=><li key={index}>{warning}</li>)}</ul></details>}
      <Button variant="contained" disabled={busy||preview.duplicate} onClick={()=>void upload(true)}>Importovať</Button>
    </section>}
  </Paper>;
}

type Confirm = { title:string; text?:ReactNode; label?:string; danger?:boolean; run?:()=>Promise<unknown>; message?:string; edit?:()=>void; remove?:()=>void };
const SELECTION='kbm-logbook-selection';
function storedSelection():{vehicleId?:number;year?:number;month?:number} {
  try { return JSON.parse(sessionStorage.getItem(SELECTION)||'{}'); } catch { return {}; }
}
type ThemeProps = { themePref: ThemePref; setThemePref: (p: ThemePref) => void };
function Workspace({account,signOut,themePref,setThemePref}:{account:Account;signOut:()=>void}&ThemeProps) {
  const navigate=useNavigate();
  const location=useLocation();
  // návod: pri prvom otvorení sám, potom tlačidlom s otáznikom; hodnota je stránka, na ktorú sa po ňom vrátime
  const [tour,setTour]=useState<string|null>(null);
  useEffect(()=>{
    if(tourSeen(account.username))return;
    const timer=setTimeout(()=>setTour(window.location.pathname.replace(/^\/vehicle-logbook/,'')||'/'),700);
    return ()=>clearTimeout(timer);
  },[account.username]);
  const phone=useMediaQuery('(max-width:700px)');
  const [vehicles,setVehicles]=useState<Row[]>([]);
  const [loaded,setLoaded]=useState(false);
  const [vehicleId,setVehicleId]=useState<number|undefined>(()=>storedSelection().vehicleId);
  const [year,setYear]=useState(()=>storedSelection().year??Number(localDay().slice(0,4)));
  const [month,setMonth]=useState(()=>storedSelection().month??Number(localDay().slice(5,7)));
  // Vybrané vozidlo a mesiac ostanú aj po obnovení stránky (mesačný sprievodca je na ne naviazaný).
  useEffect(()=>{
    try { sessionStorage.setItem(SELECTION,JSON.stringify({vehicleId,year,month})); } catch { /* bez úložiska */ }
  },[vehicleId,year,month]);
  const [history,setHistory]=useState<Report[]>([]);
  const [destinations,setDestinations]=useState<Row[]>([]);
  const [users,setUsers]=useState<Account[]>([]);
  const [audit,setAudit]=useState<Row[]>([]);
  const [busy,setBusy]=useState(true);
  const [problem,setProblem]=useState('');
  const [notice,setNotice]=useState('');
  const [refresh,setRefresh]=useState(0);
  const [edit,setEdit]=useState<Edit>();
  const [userEdit,setUserEdit]=useState<Account>();
  const [removing,setRemoving]=useState<Row>();
  const [confirm,setConfirm]=useState<Confirm>();
  const [committing,setCommitting]=useState(false);
  const [search,setSearch]=useState('');
  const [descending,setDescending]=useState(false);
  const [annual,setAnnual]=useState(false);
  const [preview,setPreview]=useState(false);   // náhľad PDF výkazu v okne
  const [invoice,setInvoice]=useState(false);   // dialóg faktúry z tankovacích kariet (správca)
  const [signed,setSigned]=useState(()=>localDay());   // dátum pri podpise vo výkaze; prázdny = ručne
  const [demoBusy,setDemoBusy]=useState(false);
  const [menu,setMenu]=useState<HTMLElement|null>(null);   // ponuka účtu na telefóne
  const vehicle=vehicles.find(v=>v.id===vehicleId);
  const report=history[month-1];
  const path='/vehicles/'+vehicleId;
  const ready=Boolean(vehicle && report && !busy);
  const current=NAV.find(item=>item.url===location.pathname);
  const thisMonth=localDay().slice(0,7);
  const lastFinished=(()=>{const p=shiftMonth(Number(thisMonth.slice(0,4)),Number(thisMonth.slice(5,7)),-1);return monthKey(p.year,p.month);})();
  function reload(message='Uložené.') {
    setEdit(undefined);setUserEdit(undefined);setNotice(message);setRefresh(value=>value+1);
  }
  useEffect(()=>{
    let active=true;
    void api.get('/vehicles').then(({data}:{data:Row[]})=>{
      if (!active) return;
      setVehicles(data);setLoaded(true);
      setVehicleId(value=>data.some(v=>v.id===value)?value:data[0]?.id);
    }).catch(error=>{if(active)setProblem(errorText(error));});
    if(account.admin) {
      void Promise.all([api.get('/users'),api.get('/audit')]).then(([people,changes])=>{
        if(active){setUsers(people.data);setAudit(changes.data);}
      }).catch(error=>{if(active)setProblem(errorText(error));});
    }
    return ()=>{active=false;};
  },[refresh,account.admin]);
  useEffect(()=>{
    let active=true;
    if (!vehicleId) {setBusy(false);setHistory([]);setDestinations([]);return;}
    setBusy(true);
    void Promise.all([api.get(path+'/reports',{params:{year}}),api.get(path+'/destinations')])
      .then(([reports,places])=>{if(active){setHistory(reports.data);setDestinations(places.data);}})
      .catch(error=>{if(active)setProblem(errorText(error));})
      .finally(()=>{if(active)setBusy(false);});
    return ()=>{active=false;};
  },[path,vehicleId,year,refresh]);
  // Mesačný sprievodca pracuje iba so skončenými mesiacmi, aj po obnovení či priamom odkaze.
  useEffect(()=>{
    if(location.pathname==='/mesacny-navrh' && monthKey(year,month)>lastFinished) {
      setYear(Number(lastFinished.slice(0,4)));setMonth(Number(lastFinished.slice(5,7)));
    }
  },[location.pathname,year,month,lastFinished]);
  const closed=Boolean(report?.closed);
  function askDelete(kind:'trips'|'fuel'|'destinations',row:Row) {
    const text = kind==='trips' ? <>Jazda {dayLabel(row.day)} {row.destination ? <>do <b>{String(row.destination)}</b></> : ''} sa odstráni.
      Nasledujúca jazda bude začínať na stave tachometra pred touto jazdou, takže jej kilometre sa predĺžia.</>
      : kind==='fuel' ? <>Tankovanie {dayLabel(row.day)} ({fmt(row.purchased)} l, {fmt(row.gross)} €) sa odstráni.</>
      : <>Cieľ <b>{String(row.name)}</b> sa odstráni zo zoznamu. Zapísané jazdy ostanú.</>;
    setEdit(undefined);
    setConfirm({title:kind==='trips'?'Odstrániť jazdu?':kind==='fuel'?'Odstrániť tankovanie?':'Odstrániť cieľ?',text,
      label:'Odstrániť',danger:true,message:'Odstránené.',run:()=>api.delete(path+'/'+kind+'/'+row.id)});
  }
  function rowEdit(kind:'trips'|'fuel',row?:Entry) {
    const fields=kind==='trips'?tripFields:fuelFields;
    const title=kind==='trips'?(row?'Upraviť jazdu':'Nová jazda'):(row?'Upraviť tankovanie':'Nové tankovanie');
    const settings={title,fields,path:path+'/'+kind+(row?'/'+row.id:''),editing:Boolean(row),vehicleId,destinations,
      onDelete:row?()=>askDelete(kind,row):undefined};
    if (row) {
      const { warnings: _warnings, ...values } = row;
      // pri jazde: stav nádrže pred ňou a normy vozidla, aby okno ukázalo spotrebu a nádrž po zmene km
      const fuel=kind==='trips'&&vehicle?{before:Number(row.end_liters)+Number(row.consumed),city:Number(vehicle.city_rate),outside:Number(vehicle.outside_rate)}:undefined;
      setEdit({...settings,fuel,initial:{...values,liters:row.purchased,unit_price:'',clock:String(row.clock??'').slice(0,5)}});
      return;
    }
    const today=localDay();
    const day=today.startsWith(String(report?.month))?today:String(report?.month)+'-01';
    void api.get(path+'/odometer',{params:{day}}).then(({data})=>{
      setEdit({...settings,initial:{day,category:'mesto',start_odo:data.odo,vat:data.vat}});
    }).catch(error=>setProblem(errorText(error)));
  }
  function openRow(kind:'trips'|'fuel',row:Entry) {
    if (!closed) { rowEdit(kind,row); return; }
    const lines = kind==='trips'
      ? [['Trasa',[row.origin,row.destination].filter(Boolean).join(' → ')||'—'],['Tachometer',fmt(row.start_odo)+' → '+fmt(row.end_odo)+' km'],
         ['Vzdialenosť',fmt(row.distance)+' km'],['Účel',row.purpose],['Poznámka',row.note]]
      : [['Stanica',row.station],['Natankované',fmt(row.purchased)+' l'],['Suma s DPH',fmt(row.gross)+' €'],['Tachometer',fmt(row.odo)],['Poznámka',row.note]];
    setConfirm({title:(kind==='trips'?'Jazda ':'Tankovanie ')+dayLabel(row.day),text:<>
      <dl className="detail">{lines.filter(([,v])=>v).map(([k,v])=><div key={String(k)}><dt>{String(k)}</dt><dd>{String(v)}</dd></div>)}</dl>
      <Alert severity="info" icon={<Icon name="lock"/>}>Mesiac je uzavretý, záznam nemožno meniť.</Alert></>});
  }
  async function addDemo() {
    setDemoBusy(true);setProblem('');
    try {
      const {data}=await api.post('/demo');
      setVehicleId(data.vehicle_id);setYear(data.year);setMonth(data.month);
      reload('Ukážkové vozidlo je pripravené. Skúste vyplniť mesiac od 12 240 do 13 280 km.');
      navigate('/mesacny-navrh');
    } catch(error){setProblem(errorText(error));} finally{setDemoBusy(false);}
  }
  function exportLink(format:string,kind='report',wholeYear=false) {
    return '/vehicle-logbook/api'+path+'/export?year='+year+(wholeYear?'':'&month='+month)+'&format='+format+'&kind='+kind+(kind==='report'?'&signed='+signed:'');
  }
  function newVehicle() {
    setEdit({title:'Nové vozidlo',path:'/vehicles',fields:vehicleFields,
      initial:{start_date:localDay(),initial_odo:0,initial_liters:0,initial_net:0,city_limit:60,kind:'osobné'}});
  }
  function monthAction(reopening:boolean) {
    setConfirm(reopening
      ? {title:'Otvoriť mesiac?',text:<>{monthName(month)} {year} sa znova otvorí na úpravy. Ak sú uzavreté aj neskoršie mesiace, otvoria sa tiež.</>,label:'Otvoriť',
         message:'Mesiac je otvorený.',run:()=>api.post(path+'/reopen',null,{params:{month:report?.month+'-01'}})}
      : {title:'Uzavrieť mesiac?',text:<>Uzavrú sa všetky mesiace do konca obdobia {monthName(month)} {year}. Záznamy v nich už nepôjde meniť, kým správca uzávierku neotvorí.</>,
         label:'Uzavrieť',message:'Mesiac je uzavretý.',run:()=>api.post(path+'/close',null,{params:{month:report?.month+'-01'}})});
  }

  const noVehicle = loaded && !vehicles.length ? <Empty title="Zatiaľ tu nie je žiadne vozidlo">
    {account.admin ? <><p>Pridajte prvé vozidlo alebo si všetko vyskúšajte na ukážkových údajoch.</p>
      <div className="actions center"><Button variant="contained" startIcon={<Icon name="plus"/>} onClick={newVehicle}>Pridať vozidlo</Button>
        <Button variant="outlined" disabled={demoBusy} onClick={()=>void addDemo()}>Vyskúšať s ukážkovými údajmi</Button></div></>
      : <p>Nemáte priradené žiadne vozidlo. Požiadajte správcu, aby vám ho sprístupnil.</p>}
  </Empty> : null;
  const loading = !noVehicle && !ready ? <div className="spinner"><CircularProgress size={30} /></div> : null;
  const gate = noVehicle ?? loading;

  const issues=report?.rows.flatMap(row=>row.warnings.map(warning=>dayLabel(row.day)+': '+warning))??[];
  const issuesPanel=issues.length>0&&<Alert severity="warning" icon={<Icon name="alert"/>} className="issues"><details>
    <summary>{issues.length} {plural(issues.length,'vec','veci','vecí')} na kontrolu v tomto mesiaci</summary>
    <ul>{issues.map((issue,index)=><li key={index}>{issue}</li>)}</ul>
  </details></Alert>;
  const lockChip=closed&&<Chip size="small" icon={<Icon name="lock" size={14}/>} label="Uzavretý mesiac" className="lock-chip" />;
  const lockedChip=closed&&<>{lockChip}
    {account.admin&&<Button size="small" variant="outlined" startIcon={<Icon name="lock" size={16}/>} onClick={()=>monthAction(true)}>Otvoriť mesiac</Button>}</>;

  function renderRecords(kind:'trips'|'fuel') {
    const isTrip=kind==='trips';
    // Jazdy ukazujú aj tankovania (červeným); l/100 km = litre tankovania / km od predošlého (per100 z backendu)
    const all=(report?.rows??[]).filter(row=>row.type===(isTrip?'trip':'fuel'));
    const listed=isTrip?(report?.rows??[]):all;
    const per100=(row:Entry)=>row.per100==null?null:Number(row.per100);
    const used=(row:Entry)=>per100(row)==null?null:per100(row)!*Number(row.distance)/100;
    const needle=search.trim().toLocaleLowerCase('sk');
    const rows=listed.filter(row=>!needle||[row.destination,row.origin,row.station,row.note,row.purpose]
      .some(value=>String(value??'').toLocaleLowerCase('sk').includes(needle)));
    if(descending) rows.reverse();
    const totalKm=all.reduce((s,r)=>s+Number(r.distance??0),0);
    const totalL=all.reduce((s,r)=>s+Number(r.purchased??0),0), totalE=all.reduce((s,r)=>s+Number(r.gross??0),0);
    const warn=(row:Entry)=>row.warnings.length>0&&<Tooltip title={row.warnings.join(' ')}><span className="row-warn"><Icon name="alert" size={16}/></span></Tooltip>;
    const actions=(row:Entry)=>!closed&&<span className="row-actions" onClick={e=>e.stopPropagation()}>
      <Tooltip title="Upraviť"><IconButton size="small" onClick={()=>rowEdit(kindOf(row),row)} aria-label="Upraviť"><Icon name="edit" size={17}/></IconButton></Tooltip>
      <Tooltip title="Odstrániť"><IconButton size="small" onClick={()=>askDelete(kindOf(row),row)} aria-label="Odstrániť"><Icon name="trash" size={17}/></IconButton></Tooltip></span>;
    const route=(row:Entry)=>[row.origin,row.destination].filter(Boolean).join(' → ')||'Bez cieľa';
    const kindOf=(row:Entry)=>row.type==='fuel'?'fuel' as const:'trips' as const;
    const open=(row:Entry)=>({onClick:()=>openRow(kindOf(row),row),tabIndex:0,role:'button',
      onKeyDown:(e:KeyboardEvent)=>{if(e.key==='Enter')openRow(kindOf(row),row);}});
    const estimateNote='Predbežne podľa najbližšieho tankovania, spresní sa po ďalšom tankovaní';
    const approx=(row:Entry)=>row.per100_estimate?'≈ ':'';
    const tripUse=(row:Entry)=>per100(row)==null?<span className="muted">—</span>
      :<span title={row.per100_estimate?estimateNote:undefined}>{approx(row)}{fmt(per100(row),1)} l/100 km<small className="muted block">{fmt(used(row))} l</small></span>;
    return <>
      <PageTitle title={isTrip?'Jazdy':'Tankovanie'}>
        {!isTrip&&account.admin&&<Button variant="outlined" startIcon={<Icon name="upload"/>} onClick={()=>setInvoice(true)}>Faktúra z kariet</Button>}
        {ready&&<><Button variant="outlined" startIcon={<Icon name="download"/>} href={exportLink('xlsx',kind)}>Excel</Button>
          <Button variant="contained" startIcon={<Icon name="plus"/>} disabled={closed} onClick={()=>rowEdit(kind)}>{isTrip?'Pridať jazdu':'Pridať tankovanie'}</Button></>}
      </PageTitle>
      {gate}{ready&&<>
        {all.length>0&&<div className="toolbar">
          <TextField size="small" placeholder={isTrip?'Hľadať cieľ, účel…':'Hľadať stanicu…'} value={search} onChange={e=>setSearch(e.target.value)}
            slotProps={{input:{startAdornment:<span className="search-icon"><Icon name="search" size={16}/></span>}}}/>
          {phone?<Tooltip title={descending?'Najnovšie hore':'Najstaršie hore'}><IconButton onClick={()=>setDescending(v=>!v)} aria-label={descending?'Najnovšie hore':'Najstaršie hore'}><Icon name="sort"/></IconButton></Tooltip>
            :<Button size="small" startIcon={<Icon name="sort" size={16}/>} onClick={()=>setDescending(v=>!v)}>{descending?'Najnovšie hore':'Najstaršie hore'}</Button>}
          {lockedChip}
          <span className="toolbar-sum">{isTrip?<>{all.length} {plural(all.length,'jazda','jazdy','jázd')} · <b>{fmt(totalKm)} km</b></>:<>{all.length}× · <b>{fmt(totalL)} l</b> · <b>{fmt(totalE)} €</b></>}</span>
        </div>}
        {issuesPanel}
        {!all.length ? <Empty icon={isTrip?'route':'fuel'} title={isTrip?'V tomto mesiaci zatiaľ nie sú jazdy':'V tomto mesiaci zatiaľ nie je tankovanie'}>
            {!closed&&<><p>{isTrip?'Pridajte jazdu ručne alebo nechajte sprievodcu navrhnúť celý mesiac.':'Pridajte tankovanie z bločku.'}</p>
            <div className="actions center"><Button variant="contained" startIcon={<Icon name="plus"/>} onClick={()=>rowEdit(kind)}>{isTrip?'Pridať jazdu':'Pridať tankovanie'}</Button>
              {isTrip&&monthKey(year,month)<=lastFinished&&<Button variant="outlined" startIcon={<Icon name="wand"/>} onClick={()=>navigate('/mesacny-navrh')}>Vyplniť mesiac sprievodcom</Button>}</div></>}
          </Empty>
        : !rows.length ? <Empty icon="search" title="Nič sa nenašlo"><p>Skúste iné slovo.</p></Empty>
        : phone ? <div className="cards">{rows.map(row=>isTrip&&row.type==='fuel'
          ? <Paper variant="outlined" key={'f'+String(row.id)} className="card-row refuel-row" {...open(row)}>
            <div className="card-top"><span className="card-day">{dayLabel(row.day)}</span>{warn(row)}
              <b className="card-big">{fmt(row.purchased)} l</b></div>
            <div className="card-main"><Icon name="fuel" size={15}/> Tankovanie{row.station?' · '+String(row.station):''}</div>
            <div className="card-sub">{fmt(row.gross)} €{per100(row)!=null?<> · <b>{fmt(per100(row),1)} l/100 km</b> za {fmt(row.window_km)} km</>:''} · v nádrži {fmt(row.end_liters,1)} l</div>
          </Paper>
          : <Paper variant="outlined" key={String(row.id)} className="card-row" {...open(row)}>
            <div className="card-top"><span className="card-day">{dayLabel(row.day)}</span>{warn(row)}
              <b className="card-big">{isTrip?fmt(row.distance)+' km':fmt(row.purchased)+' l'}</b></div>
            <div className="card-main">{isTrip?route(row):(row.station||'Tankovanie')}</div>
            <div className="card-sub">{isTrip?<>{fmt(row.start_odo)} → {fmt(row.end_odo)} km · {row.category==='mesto'?'v meste':'mimo mesta'}{row.estimated?' · odhad':''}</>
              :<>{fmt(row.gross)} € · {fmt(row.unit_price,3)} €/l{row.odo!=null?' · '+fmt(row.odo)+' km':''}</>}</div>
            <div className="card-sub">{isTrip?<>{per100(row)!=null?<>{approx(row)}{fmt(per100(row),1)} l/100 km · spotreba {fmt(used(row))} l · </>:''}v nádrži {fmt(row.end_liters,1)} l</>
              :<>{per100(row)!=null?<>{fmt(per100(row),1)} l/100 km za {fmt(row.window_km)} km · </>:''}v nádrži po natankovaní {fmt(row.end_liters,1)} l</>}</div>
          </Paper>)}</div>
        : <Paper variant="outlined" className="table-wrap"><table className="records">
            <thead><tr><th>Dátum</th>{isTrip?<><th>Trasa</th><th>Tachometer</th><th className="num">Km</th><th className="num" title="Litre tankovania / km od predošlého tankovania">Spotreba</th><th className="num" title="Stav nádrže po jazde">V nádrži</th></>
              :<><th>Stanica</th><th className="num">Litre</th><th className="num">Suma s DPH</th><th className="num">Cena/l</th><th className="num">Tachometer</th><th className="num" title="Natankované litre / km od predošlého tankovania">Priemer</th><th className="num" title="Stav nádrže po natankovaní">V nádrži</th></>}<th/></tr></thead>
            <tbody>{rows.map(row=>isTrip&&row.type==='fuel'
              ? <tr key={'f'+String(row.id)} {...open(row)} className="refuel-row">
                <td className="nowrap">{dayLabel(row.day)} {warn(row)}</td>
                <td><span className="route"><Icon name="fuel" size={15}/> Tankovanie {fmt(row.purchased)} l</span>
                  <small className="block">{[row.station,fmt(row.gross)+' €'].filter(Boolean).join(' · ')}</small></td>
                <td className="nowrap">{row.odo!=null?fmt(row.odo):''}</td>
                <td className="num">{per100(row)!=null?<small>{fmt(row.window_km)} km</small>:''}</td>
                <td className="num nowrap">{per100(row)!=null?<b>{fmt(per100(row),1)} l/100 km</b>:'—'}</td>
                <td className="num">{fmt(row.end_liters,1)} l</td>
                <td className="actions-cell">{actions(row)}</td></tr>
              : <tr key={String(row.id)} {...open(row)} className={row.nonworking?'weekend':undefined}>
              <td className="nowrap">{dayLabel(row.day)} {warn(row)}</td>
              {isTrip?<><td><span className="route">{route(row)}</span>
                  <small className="muted block">{[row.category==='mesto'?'v meste':'mimo mesta',row.purpose,row.estimated&&String(row.source||'odhad')].filter(Boolean).join(' · ')}</small></td>
                <td className="nowrap muted">{fmt(row.start_odo)} → {fmt(row.end_odo)}</td>
                <td className="num"><b>{fmt(row.distance)}</b></td><td className="num nowrap">{tripUse(row)}</td><td className="num muted">{fmt(row.end_liters,1)} l</td></>
              :<><td>{row.station||'—'}{row.note&&<small className="muted block">{String(row.note)}</small>}</td><td className="num"><b>{fmt(row.purchased)}</b></td>
                <td className="num">{fmt(row.gross)} €</td><td className="num">{fmt(row.unit_price,3)} €</td><td className="num muted">{fmt(row.odo)}</td><td className="num nowrap">{per100(row)!=null?<>{fmt(per100(row),1)} l/100 km<small className="muted block">{fmt(row.window_km)} km</small></>:'—'}</td><td className="num muted">{fmt(row.end_liters,1)} l</td></>}
              <td className="actions-cell">{actions(row)}</td></tr>)}</tbody>
          </table></Paper>}
      </>}
    </>;
  }

  const max=Math.max(1,...history.map(row=>Number(row.gross)));
  const total=Object.fromEntries(Object.keys(metricLabels).map(key=>[key,history.reduce((sum,r)=>sum+Number(r[key]??0),0)])) as Row;
  total.average=Number(total.distance)?Number(total.consumption)*100/Number(total.distance):0;
  total.gross_per_liter=Number(total.purchased)?Number(total.gross)/Number(total.purchased):0;
  for(const key of ['opening_liters','opening_odo','opening_net']) total[key]=history[0]?.[key];
  for(const key of ['closing_liters','closing_odo','closing_net']) total[key]=history[11]?.[key];
  const tank=vehicle&&report?Math.max(0,Math.min(100,100*Number(report.closing_liters)/Number(vehicle.capacity))):0;
  const tankOver=Boolean(vehicle&&report)&&Number(report!.closing_liters)>Number(vehicle!.capacity)*1.03+1e-9&&Number(report!.closing_liters)>Number(vehicle!.capacity)+1;
  const recent=(report?.rows??[]).slice(-6).reverse();
  const wizardAllowed=monthKey(year,month)<=lastFinished;

  const home=<>
    <PageTitle title="Prehľad" />
    {gate}{ready&&<>
      <div className="quick" data-tour="quick">
        <button type="button" className="quick-tile" disabled={closed} onClick={()=>rowEdit('trips')}><span><Icon name="route" size={22}/></span><b>Pridať jazdu</b><small>Jedna cesta autom</small></button>
        <button type="button" className="quick-tile" disabled={closed} onClick={()=>rowEdit('fuel')}><span><Icon name="fuel" size={22}/></span><b>Pridať tankovanie</b><small>Z bločku</small></button>
        <button type="button" className="quick-tile accent" onClick={()=>navigate('/mesacny-navrh')}><span><Icon name="wand" size={22}/></span><b>Vyplniť celý mesiac</b><small>Bločky + krátky popis, návrh urobí asistent</small></button>
      </div>
      {issuesPanel}
      <div className="stats" data-tour="stats">
        <MetricCard icon="route" label="Najazdené" value={fmt(report.distance)} unit="km" sub={`${fmt(report.trip_count)} ${plural(Number(report.trip_count),'jazda','jazdy','jázd')}`} />
        <MetricCard icon="gauge" label="Spotreba" value={fmt(report.consumption)} unit="l" sub={Number(report.distance)?`${fmt(report.average)} l/100 km`:'bez jázd'} />
        <MetricCard icon="money" label="Tankovanie" value={fmt(report.gross)} unit="€" sub={`${fmt(report.purchased)} l`} />
        <MetricCard icon="fuel" label="V nádrži na konci" value={fmt(report.closing_liters,1)} unit="l"
          sub={tankOver?<span className="stat-sub over">viac ako nádrž ({fmt(vehicle?.capacity)} l) – chýbajú jazdy</span>:`z ${fmt(vehicle?.capacity)} l`}>
          <span className={'tank'+(tankOver?' over':'')}><i style={{width:tank+'%'}}/></span></MetricCard>
      </div>
      <div className="two-col">
        <Paper variant="outlined" className="panel"><div className="section-heading"><h2>Posledné záznamy</h2>
          {recent.length>0&&<Button size="small" onClick={()=>navigate('/jazdy')}>Všetky jazdy</Button>}</div>
          {recent.length?<ul className="recent">{recent.map(row=><li key={row.type+String(row.id)}>
            <span className={'recent-icon '+String(row.type)}><Icon name={row.type==='trip'?'route':'fuel'} size={16}/></span>
            <span><b>{row.type==='trip'?([row.origin,row.destination].filter(Boolean).join(' → ')||'Jazda'):(row.station||'Tankovanie')}</b>
              <small>{dayLabel(row.day)}</small></span>
            <span className="recent-val">{row.type==='trip'?fmt(row.distance)+' km':fmt(row.purchased)+' l · '+fmt(row.gross)+' €'}</span></li>)}</ul>
          :<p className="muted">V tomto mesiaci zatiaľ nič nie je.</p>}
        </Paper>
        <Paper variant="outlined" className="panel"><h2>Náklady na palivo {year}</h2>
          <div className="chart">{history.map((row,index)=><button type="button" key={String(row.month)} className={'chart-row'+(index+1===month?' on':'')}
            onClick={()=>setMonth(index+1)} aria-label={'Vybrať '+monthName(index+1)}>
            <span>{monthName(index+1).slice(0,3)}</span>
            <span className="bar-track"><i style={{width:100*Number(row.gross)/max+'%'}} /></span>
            <span className="num">{Number(row.gross)?fmt(row.gross,0)+' €':'–'}</span></button>)}</div>
        </Paper>
      </div>
    </>}
  </>;

  const reports=<>
    <PageTitle title="Výkazy">
      {ready&&<><TextField type="date" size="small" label="Dátum podpisu" value={signed} onChange={event=>setSigned(event.target.value)}
          slotProps={{inputLabel:{shrink:true}}} className="sign-date" sx={{width:{xs:'100%',sm:170}}} title="Dátum pri „V Žiline dňa“. Vymažte ho, ak ho chcete dopísať ručne."/>
        <Button variant="outlined" startIcon={<Icon name="report"/>} {...(phone?{href:exportLink('pdf','report',annual)+'&inline=1',target:'_blank'}:{onClick:()=>setPreview(true)})}>Náhľad</Button>
        <Button variant="outlined" startIcon={<Icon name="download"/>} href={exportLink('pdf','report',annual)}>PDF</Button>
        <Button variant="contained" startIcon={<Icon name="download"/>} href={exportLink('xlsx','report',annual)}>Excel</Button></>}
    </PageTitle>
    {gate}{ready&&<>
      <div className="toolbar">
        <div className="segmented" role="tablist">
          <button type="button" role="tab" aria-selected={!annual} className={!annual?'on':''} onClick={()=>setAnnual(false)}>{monthName(month)}</button>
          <button type="button" role="tab" aria-selected={annual} className={annual?'on':''} onClick={()=>setAnnual(true)}>Celý rok {year}</button>
        </div>
        {!annual&&lockChip}
        {!annual&&account.admin&&(closed||String(report.month)<thisMonth)&&<Button variant="outlined" size="small" startIcon={<Icon name="lock" size={16}/>}
          onClick={()=>monthAction(closed)}>{closed?'Otvoriť mesiac':'Uzavrieť mesiac'}</Button>}
      </div>
      {(annual?history.some(row=>Number(row.estimated_count)>0):Number(report.estimated_count)>0)&&
        <Alert severity="info">Výkaz obsahuje jazdy odhadnuté z približných podkladov, v exporte sú označené.</Alert>}
      {!annual&&issuesPanel}
      {METRIC_GROUPS.map(([title,icon,keys])=><Paper variant="outlined" className="panel metric-group" key={title}>
        <h2><Icon name={icon}/> {title}</h2>
        <dl className="metric-list">{keys.map(key=><div key={key}><dt>{metricLabels[key][0]}</dt>
          <dd>{fmt((annual?total:report)[key],key==='gross_per_liter'?3:2)} <small>{metricLabels[key][1]}</small></dd></div>)}</dl>
      </Paper>)}
      {annual&&<Paper variant="outlined" className="table-wrap"><table className="records static">
        <thead><tr><th>Mesiac</th><th className="num">Km</th><th className="num">Spotreba (l)</th><th className="num">Natankované (l)</th><th className="num">Za palivo (€)</th><th className="num">V nádrži (l)</th><th>Stav</th></tr></thead>
        <tbody>{history.map((r,i)=><tr key={String(r.month)} onClick={()=>{setMonth(i+1);setAnnual(false);}}>
          <td>{monthName(i+1)}</td><td className="num">{fmt(r.distance)}</td><td className="num">{fmt(r.consumption)}</td><td className="num">{fmt(r.purchased)}</td>
          <td className="num">{fmt(r.gross)}</td><td className="num">{fmt(r.closing_liters)}</td><td>{r.closed?<span className="muted"><Icon name="lock" size={14}/> uzavretý</span>:''}</td></tr>)}</tbody>
      </table></Paper>}
    </>}
  </>;

  const vehiclesPage=<>
    <PageTitle title="Vozidlá">{account.admin&&<Button variant="contained" startIcon={<Icon name="plus"/>} onClick={newVehicle}>Pridať vozidlo</Button>}</PageTitle>
    {noVehicle}
    <div className="vehicle-grid">{vehicles.map(v=><Paper variant="outlined" key={String(v.id)} className={'vehicle-card'+(v.id===vehicleId?' selected':'')}>
      <div className="vehicle-head"><span className="plate">{String(v.registration)}</span>{v.id===vehicleId&&<Chip size="small" color="primary" icon={<Icon name="check" size={14}/>} label="Vybrané"/>}</div>
      <h3>{String(v.name)}</h3>
      <p className="muted">{[v.kind,v.fuel_type,v.responsible].filter(Boolean).join(' · ')||'—'}</p>
      <dl className="vehicle-facts"><div><dt>Spotreba mesto / mimo</dt><dd>{fmt(v.city_rate)} / {fmt(v.outside_rate)} l</dd></div>
        <div><dt>Nádrž</dt><dd>{fmt(v.capacity)} l</dd></div><div><dt>Evidencia od</dt><dd>{dateLabel(v.start_date)}</dd></div></dl>
      <div className="vehicle-actions">
        {v.id!==vehicleId&&<Button size="small" variant="outlined" onClick={()=>{setVehicleId(Number(v.id));setNotice('Vybrané vozidlo '+String(v.registration)+'.');}}>Vybrať</Button>}
        {account.admin&&<><Button size="small" startIcon={<Icon name="edit" size={16}/>} onClick={()=>setEdit({title:'Upraviť vozidlo',path:'/vehicles/'+v.id,
          fields:vehicleFields,initial:v,editing:true})}>Upraviť</Button>
          <Button size="small" color="error" startIcon={<Icon name="trash" size={16}/>} onClick={()=>setRemoving(v)}>Odstrániť</Button></>}
      </div>
    </Paper>)}</div>
    {vehicle&&<Paper variant="outlined" className="panel"><div className="section-heading"><div><h2><Icon name="pin"/> Časté ciele · {String(vehicle.registration)}</h2>
      <p className="muted">Pri zápise jazdy sa z nich doplnia kilometre.</p></div>
      <Button variant="outlined" startIcon={<Icon name="plus"/>} onClick={()=>setEdit({title:'Nový cieľ',path:path+'/destinations',
        fields:destinationFields,initial:{}})}>Pridať cieľ</Button></div>
      {destinations.length?<table className="records places">
        <thead><tr><th>Cieľ</th><th className="wide-only">Odkiaľ</th><th className="num">{phone?'Km':'Jedným smerom'}</th><th className="wide-only"/></tr></thead>
        <tbody>{destinations.map(r=><tr key={String(r.id)} tabIndex={0} role="button" onClick={()=>setEdit({title:'Upraviť cieľ',
            path:path+'/destinations/'+r.id,fields:destinationFields,initial:r,editing:true,onDelete:()=>askDelete('destinations',r)})}>
          <td><b>{String(r.name)}</b>{(phone&&r.origin||r.note)&&<small className="muted block">{[phone&&r.origin?'z '+String(r.origin):'',String(r.note??'')].filter(Boolean).join(' · ')}</small>}</td>
          <td className="wide-only">{r.origin||'—'}</td>
          <td className="num">{fmt(r.distance)} km</td>
          <td className="actions-cell wide-only"><span className="row-actions" onClick={e=>e.stopPropagation()}>
            <Tooltip title="Odstrániť"><IconButton size="small" aria-label="Odstrániť" onClick={()=>askDelete('destinations',r)}><Icon name="trash" size={17}/></IconButton></Tooltip></span></td></tr>)}</tbody>
      </table>:<p className="muted">Zatiaľ žiadne ciele.</p>}
    </Paper>}
  </>;

  const adminPage=<>
    <PageTitle title="Správa" />
    <Paper variant="outlined" className="panel"><div className="section-heading"><div><h2><Icon name="users"/> Prístup k vozidlám</h2>
      <p className="muted">Účty pridávate v portáli KBM. Tu určíte, ktoré vozidlá kto vidí.</p></div></div>
      <table className="records">
        <thead><tr><th>Meno</th><th>Vozidlá</th><th/></tr></thead>
        <tbody>{users.map(r=><tr key={r.id} tabIndex={0} role="button" onClick={()=>setUserEdit(r)}>
          <td><b>{r.name??r.username}</b>{!r.active&&<small className="muted block">bez prístupu</small>}</td>
          <td>{r.admin?<span className="muted">všetky (správca)</span>:(r.vehicle_ids??[]).map(id=>vehicles.find(v=>v.id===id)?.registration).filter(Boolean).join(', ')||<span className="muted">žiadne</span>}</td>
          <td className="actions-cell"><Icon name="edit" size={17}/></td></tr>)}</tbody>
      </table>
    </Paper>
    <ImportPanel done={id=>{setVehicleId(id);reload('Import je uložený.');navigate('/');}}/>
    <Paper className="panel" variant="outlined"><h2><Icon name="wand"/> Ukážkové údaje</h2>
      <p className="muted">Pridá samostatné ukážkové vozidlo s jazdami a tankovaním, na ktorom si vyskúšate vyplnenie mesiaca.</p>
      <Button sx={{mt:1.5}} variant="outlined" disabled={demoBusy} onClick={()=>void addDemo()}>{demoBusy?'Pripravujem…':'Otvoriť ukážkové vozidlo'}</Button>
    </Paper>
    <Paper variant="outlined" className="panel"><details><summary><h2 className="inline"><Icon name="history"/> História zmien</h2></summary>
      <div className="table-wrap"><table className="records static">
        <thead><tr><th>Kedy</th><th>Čo</th><th>Kto</th><th>Vozidlo</th></tr></thead>
        <tbody>{audit.map(r=><tr key={String(r.id)}>
          <td className="nowrap">{r.created_at?new Date(String(r.created_at)).toLocaleString('sk-SK',{timeZone:'Europe/Bratislava',dateStyle:'short',timeStyle:'short'}):'—'}</td>
          <td>{r.action}</td><td>{users.find(u=>u.id===r.user_id)?.name??users.find(u=>u.id===r.user_id)?.username??'—'}</td>
          <td>{vehicles.find(v=>v.id===r.vehicle_id)?.registration??((r.data as unknown as Row)?.registration as string|undefined)??'—'}</td></tr>)}</tbody>
      </table></div></details>
    </Paper>
  </>;

  const initials=(account.name??account.username).split(/\s+/).map(p=>p[0]).join('').slice(0,2).toUpperCase();
  const links=NAV.filter(item=>!item.admin||account.admin);
  const startTour=()=>setTour(location.pathname);
  const link=(item:NavItem,short=false)=><NavLink key={item.url} to={item.url} end={item.url==='/'} data-tour={'nav'+item.url} onClick={()=>setSearch('')}>
    <Icon name={item.icon} size={short?22:17}/><span>{short?item.short??item.label:item.label}</span></NavLink>;
  return <div className={'app'+(phone?' phone':'')}>
    <header className="topbar">
      <NavLink className="brand" to="/" aria-label="Kniha jázd – prehľad"><img src="/vehicle-logbook/kbm-logo.png" alt="KBM"/><span className="brand-divider"/><span>Kniha jázd</span></NavLink>
      <a href="/" className="portal-link" data-tour="portal" title="Hlavná stránka portálu KBM" aria-label="Všetky aplikácie"><Icon name="grid" size={17}/><span>Všetky aplikácie</span></a>
      {!phone&&<nav aria-label="Hlavné menu">{links.map(item=>link(item))}</nav>}
      {phone ? <div className="account">
        <IconButton className="avatar-button" data-tour="account" aria-label="Účet a ďalšie stránky" onClick={e=>setMenu(e.currentTarget)}><span className="avatar">{initials}</span></IconButton>
        <Menu anchorEl={menu} open={Boolean(menu)} onClose={()=>setMenu(null)} anchorOrigin={{vertical:'bottom',horizontal:'right'}} transformOrigin={{vertical:'top',horizontal:'right'}}>
          <MenuItem disabled className="menu-account">{account.name??account.username} · {account.admin?'Správca':'Používateľ'}</MenuItem>
          {links.filter(item=>!item.context).map(item=><MenuItem key={item.url} selected={location.pathname===item.url}
            onClick={()=>{setMenu(null);setSearch('');navigate(item.url);}}><Icon name={item.icon}/>&nbsp;&nbsp;{item.label}</MenuItem>)}
          <MenuItem onClick={()=>setThemePref(nextTheme[themePref])}><Icon name={themePref==='light'?'sun':themePref==='dark'?'moon':'monitor'}/>&nbsp;&nbsp;Vzhľad: {themeLabels[themePref]}</MenuItem>
          <MenuItem onClick={()=>{setMenu(null);startTour();}}><Icon name="help"/>&nbsp;&nbsp;Návod</MenuItem>
          <MenuItem onClick={signOut}><Icon name="logout"/>&nbsp;&nbsp;Odhlásiť</MenuItem>
        </Menu>
      </div> : <div className="account">
        <ThemeToggle pref={themePref} setPref={setThemePref}/>
        <Tooltip title="Spustiť návod"><IconButton className="theme-toggle" data-tour="help" aria-label="Návod" onClick={startTour}><Icon name="help"/></IconButton></Tooltip>
        <span className="avatar" title={account.name??account.username}>{initials}</span>
        <span className="account-name">{account.name??account.username}<small>{account.admin?'Správca':'Používateľ'}</small></span>
        <Tooltip title="Odhlásiť"><IconButton className="logout" onClick={signOut} aria-label="Odhlásiť"><Icon name="logout"/></IconButton></Tooltip>
      </div>}
    </header>
    {current?.context&&vehicles.length>0&&<div className="context-bar" data-tour="context">
      <TextField size="small" select value={vehicleId??''} onChange={e=>setVehicleId(Number(e.target.value))} className="vehicle-select"
        slotProps={{htmlInput:{'aria-label':'Vozidlo'},select:phone?{renderValue:value=>String(vehicles.find(v=>v.id===value)?.registration??'')}:undefined}}>
        {vehicles.map(v=><MenuItem value={v.id} key={v.id}><b>{String(v.registration)}</b>&nbsp;<span className="muted">{v.name!==v.registration?String(v.name):''}</span></MenuItem>)}
      </TextField>
      <MonthPicker year={year} month={month} max={location.pathname==='/mesacny-navrh'?lastFinished:undefined}
        onChange={(y,m)=>{setYear(y);setMonth(m);}} />
    </div>}
    <main className="content">
      {problem&&<Alert severity="error" onClose={()=>setProblem('')}>{problem}</Alert>}
      <Routes>
        <Route path="/" element={home} />
        <Route path="/mesacny-navrh" element={<>
          <PageTitle title="Vyplniť mesiac" />
          {gate}
          {ready&&!wizardAllowed&&<Alert severity="info">Mesiac sa dá vyplniť až po jeho skončení.</Alert>}
          {ready&&wizardAllowed&&<MonthlyWizard key={String(vehicleId)+'-'+year+'-'+month} vehicle={vehicle!}
            month={monthKey(year,month)} onSaved={()=>{setNotice('Mesiac je uložený.');setRefresh(v=>v+1);}} exportUrl={format=>exportLink(format)}/>}
        </>} />
        <Route path="/jazdy" element={renderRecords('trips')} />
        <Route path="/tankovanie" element={renderRecords('fuel')} />
        <Route path="/vykazy" element={reports} />
        <Route path="/vozidla" element={vehiclesPage} />
        {account.admin&&<Route path="/sprava" element={adminPage} />}
        <Route path="/nastavenia" element={<Navigate to={account.admin?'/sprava':'/'} replace />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </main>
    {tour!==null&&<Tour user={account.username} steps={tourSteps(account.admin,phone)} setPage={p=>{setEdit(undefined);navigate(p);}}
      onClose={()=>{navigate(tour);setTour(null);window.scrollTo(0,0);}}/>}
    {phone&&<nav className="bottom-nav" aria-label="Hlavné menu">{links.filter(item=>item.context).map(item=>link(item,true))}</nav>}
    {edit&&<RecordDialog key={edit.path} edit={edit} close={()=>setEdit(undefined)} done={saved=>{
      if(edit.path==='/vehicles'&&saved?.id)setVehicleId(Number(saved.id));
      reload();}} />}
    {preview&&<Dialog open onClose={()=>setPreview(false)} fullWidth maxWidth="lg" slotProps={{paper:{className:'pdf-preview'}}}>
      <DialogTitle>Náhľad výkazu · {String(vehicle?.registration??'')} · {annual?year:monthName(month)+' '+year}</DialogTitle>
      <DialogContent><iframe title="Náhľad výkazu" src={exportLink('pdf','report',annual)+'&inline=1'}/></DialogContent>
      <DialogActions><Button href={exportLink('pdf','report',annual)} startIcon={<Icon name="download"/>}>Stiahnuť PDF</Button>
        <Button variant="contained" onClick={()=>setPreview(false)}>Zavrieť</Button></DialogActions>
    </Dialog>}
    {invoice&&<FuelInvoiceDialog close={()=>setInvoice(false)} done={message=>{setInvoice(false);reload(message);}}/>}
    {userEdit&&<UserDialog account={userEdit} vehicles={vehicles} close={()=>setUserEdit(undefined)} done={()=>reload()}/>}
    {removing&&<DeleteVehicleDialog vehicle={removing} close={()=>setRemoving(undefined)} done={()=>{
      const plate=String(removing.registration);setRemoving(undefined);
      if(removing.id===vehicleId)setVehicleId(undefined);
      reload('Vozidlo '+plate+' a všetky jeho údaje sú odstránené.');}}/>}
    {confirm&&<Dialog open onClose={committing?undefined:()=>setConfirm(undefined)} fullWidth maxWidth="xs">
      <DialogTitle>{confirm.title}</DialogTitle>
      <DialogContent><div className="confirm-text">{confirm.text}</div></DialogContent><DialogActions>
        <Button disabled={committing} onClick={()=>setConfirm(undefined)}>{confirm.run?'Zrušiť':'Zavrieť'}</Button>
        {confirm.run&&<Button variant="contained" color={confirm.danger?'error':'primary'} disabled={committing} onClick={async()=>{
          setCommitting(true);setProblem('');
          try {await confirm.run?.();setConfirm(undefined);reload(confirm.message);}
          catch(error){setConfirm(undefined);setProblem(errorText(error));} finally{setCommitting(false);}
        }}>{confirm.label??'Potvrdiť'}</Button>}
      </DialogActions></Dialog>}
    <Snackbar open={Boolean(notice)} autoHideDuration={4000} onClose={()=>setNotice('')} anchorOrigin={{vertical:'bottom',horizontal:'center'}}>
      <Alert severity="success" variant="filled" onClose={()=>setNotice('')} icon={<Icon name="check"/>}>{notice}</Alert>
    </Snackbar>
  </div>;
}
function App({ themePref, setThemePref }: ThemeProps) {
  const [account,setAccount]=useState<Account|null>();
  const [problem,setProblem]=useState('');
  useEffect(()=>{
    let active=true;
    void api.get('/me').then(({data})=>{if(active)setAccount(data);}).catch(error=>{if(!active)return;if(error.response?.status===403)setProblem(errorText(error));else window.location.assign('/?next=vehicle-logbook');});
    const handle=api.interceptors.response.use(value=>value,error=>{
      if(error.response?.status===401)window.location.assign('/?next=vehicle-logbook');
      return Promise.reject(error);
    });
    return ()=>{active=false;api.interceptors.response.eject(handle);};
  },[]);
  if (!account) return problem ? <div className="content"><Alert severity="error" action={<Button color="inherit" size="small" href="/">Späť na portál</Button>}>{problem}</Alert></div>
    : <div className="spinner full"><CircularProgress /></div>;
  return <>{problem&&<Alert severity="error" onClose={()=>setProblem('')}>{problem}</Alert>}
    <Workspace account={account} themePref={themePref} setThemePref={setThemePref} signOut={()=>{
      void fetch('/auth/logout',{method:'POST'}).then(()=>window.location.assign('/')).catch(error=>setProblem(errorText(error)));
    }}/></>;
}
function Root() {
  const [pref,setPref,mode]=useTheme();
  return <ThemeProvider theme={themes[mode]}><CssBaseline/>
    <BrowserRouter basename="/vehicle-logbook"><App themePref={pref} setThemePref={setPref}/></BrowserRouter></ThemeProvider>;
}
createRoot(document.getElementById('root')!).render(<Root/>);

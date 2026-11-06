import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import { BrowserRouter, Navigate, NavLink, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import {
  Alert, Button, Chip, CircularProgress, CssBaseline, Dialog, DialogActions, DialogContent, DialogTitle,
  MenuItem, Paper, Stack, Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  TextField, ThemeProvider, createTheme,
} from '@mui/material';
import { api, errorText, fmt, localDay, monthName, type Account, type Entry, type Preview, type Report, type Row } from './api';
import { destinationFields, fuelFields, RecordDialog, tripFields, UserDialog, vehicleFields, type Edit } from './forms';
import { MonthlyWizard } from './MonthlyWizard';
import './style.css';

const theme = createTheme({
  palette:{primary:{main:'#1857c8'},background:{default:'#f2f6fb',paper:'#fff'},text:{primary:'#152b45',secondary:'#526780'}},
  shape:{borderRadius:12},
  typography:{fontFamily:'Inter, system-ui, -apple-system, sans-serif',button:{textTransform:'none',fontWeight:700}},
  components:{MuiPaper:{styleOverrides:{root:{backgroundImage:'none'}}},
    MuiButton:{defaultProps:{disableElevation:true}},MuiTableCell:{styleOverrides:{head:{fontWeight:750,background:'#edf3fa'}}}},
});
const nav = [
  ['/', 'Prehľad', '▦'], ['/mesacny-navrh','Mesačný sprievodca','▣'], ['/jazdy','Kniha jázd','↗'], ['/tankovanie','Tankovanie','◈'],
  ['/vykazy','Výkazy','▤'], ['/vozidla','Vozidlá a destinácie','◇'], ['/nastavenia','Nastavenia','⚙'],
];
const metricLabels: Record<string,[string,string]> = {
  distance:['Najazdené kilometre','km'], city_km:['Mesto','km'], outside_km:['Mimo mesta','km'],
  trip_count:['Počet jázd',''], opening_odo:['Počiatočný tachometer','km'], closing_odo:['Koncový tachometer','km'],
  purchased:['Nakúpené palivo','l'], calculated:['Výpočtová spotreba','l'], normative:['Normovaná spotreba','l'],
  consumption:['Bilančná spotreba','l'], average:['Priemerná spotreba','l/100 km'], gross:['Náklady s DPH','€'],
  purchase_net:['Náklady bez DPH','€'], opening_liters:['Počiatočný stav nádrže','l'], closing_liters:['Koncový stav nádrže','l'],
  correction:['Korekcia nádrže','l'], gross_per_liter:['Priemerná cena','€/l'],
  opening_net:['Počiatočná hodnota bez DPH','€'], closing_net:['Koncová hodnota bez DPH','€'],
  consumption_net:['Hodnota spotreby bez DPH','€'],
};
type Column<T> = [string,(row:T)=>ReactNode];
function Grid<T>({ columns, data, empty='V tomto prehľade zatiaľ nie sú žiadne záznamy.' }:
  {columns:Column<T>[];data:T[];empty?:string}) {
  if (!data.length) return <Paper variant="outlined" className="empty">{empty}</Paper>;
  return <TableContainer component={Paper} variant="outlined"><Table size="small">
    <TableHead><TableRow>{columns.map(([label])=><TableCell key={label}>{label}</TableCell>)}</TableRow></TableHead>
    <TableBody>{data.map((row,index)=><TableRow hover key={index}>
      {columns.map(([label,render])=><TableCell key={label}>{render(row)}</TableCell>)}
    </TableRow>)}</TableBody>
  </Table></TableContainer>;
}
function PageTitle({ kicker,title,description,children }:{kicker:string;title:string;description:string;children?:ReactNode}) {
  return <header className="page-title"><section><span className="kicker">{kicker}</span><h1>{title}</h1>
    <p>{description}</p></section>{children&&<div className="actions">{children}</div>}</header>;
}
function Metrics({ data, keys }:{data:Row;keys:string[]}) {
  return <div className="metrics">{keys.map(key=><Paper variant="outlined" key={key} className="metric">
    <span>{metricLabels[key][0]}</span><strong>{fmt(data[key],key==='gross_per_liter'?4:2)}
      <small>{metricLabels[key][1]}</small></strong></Paper>)}</div>;
}
function Login({ done }: {done:(account:Account)=>void}) {
  const [problem,setProblem]=useState('');
  const [busy,setBusy]=useState(false);
  async function submit(event:FormEvent<HTMLFormElement>) {
    event.preventDefault(); setProblem(''); setBusy(true);
    const data=Object.fromEntries(new FormData(event.currentTarget));
    try {done((await api.post('/login',data)).data);}
    catch(error){setProblem(errorText(error));} finally{setBusy(false);}
  }
  return <div className="login"><Paper component="form" onSubmit={submit} elevation={0} className="login-card">
    <Brand /><h1>Prihlásenie</h1><p>Zadajte údaje k svojmu firemnému účtu.</p>
    <Stack spacing={2.1}>
      {problem&&<Alert severity="error">{problem}</Alert>}
      <TextField label="Používateľské meno" name="username" autoComplete="username" required />
      <TextField label="Heslo" name="password" type="password" autoComplete="current-password" required />
      <Button variant="contained" size="large" type="submit" disabled={busy}>{busy?'Prihlasujem…':'Prihlásiť sa'}</Button>
    </Stack>
  </Paper></div>;
}
function Brand() {
  return <div className="brand"><span className="brand-icon" aria-hidden>↗</span><div><b>Kniha jázd</b><small>EVIDENCIA VOZIDIEL</small></div></div>;
}
function ImportPanel({ done }: {done:(vehicleId:number)=>void}) {
  const [file,setFile]=useState<File>();
  const [preview,setPreview]=useState<Preview>();
  const [problem,setProblem]=useState('');
  const [busy,setBusy]=useState(false);
  async function upload(apply:boolean) {
    if (!file) return;
    setBusy(true);setProblem('');
    const body=new FormData(); body.append('file',file);
    try {
      const {data}=await api.post('/import',body,{params:{apply,expected_digest:apply?preview?.digest:undefined}});
      if (apply) {setPreview(undefined);setFile(undefined);done(data.vehicle_id);}
      else setPreview(data);
    } catch(error){setProblem(errorText(error));} finally{setBusy(false);}
  }
  return <Paper className="panel" variant="outlined"><h2>Import pôvodného Excelu</h2>
    <p>Nahrajte prepočítaný zošit XLSM a pred uložením skontrolujte údaje a upozornenia.</p>
    <div className="actions"><Button component="label" variant="outlined" disabled={busy}>Vybrať súbor
      <input hidden type="file" accept=".xlsm" onClick={e=>{e.currentTarget.value='';}}
        onChange={e=>{setFile(e.target.files?.[0]);setPreview(undefined);setProblem('');}} />
      </Button><span>{file?.name}</span>
      <Button variant="contained" disabled={!file||busy} onClick={()=>void upload(false)}>Vytvoriť náhľad</Button>
      {busy&&<CircularProgress size={20}/>}</div>
    {problem&&<Alert severity="error" sx={{mt:2}}>{problem}</Alert>}
    {preview&&<section className="preview">
      <h3>{String(preview.vehicle.registration)} · {preview.trips.length} jázd · {preview.purchases.length} tankovaní · {preview.destinations.length} cieľov</h3>
      {preview.duplicate&&<Alert severity="warning">Vozidlo alebo súbor je už v evidencii. Import by prepísal údaje.</Alert>}
      <details open><summary>Upozornenia ({preview.warnings.length})</summary>
        {preview.warnings.length?<ul>{preview.warnings.map((warning,index)=><li key={index}>{warning}</li>)}</ul>:<p>Bez upozornení.</p>}
      </details>
      <details><summary>Jazdy určené na import</summary><Grid data={preview.trips} columns={[
        ['Dátum',r=>r.day],['Cieľ',r=>r.destination],['Od (km)',r=>fmt(r.start_odo)],['Do (km)',r=>fmt(r.end_odo)],
      ]}/></details>
      <details><summary>Tankovania určené na import</summary><Grid data={preview.purchases} columns={[
        ['Dátum',r=>r.day],['Stanica',r=>r.station],['Litre',r=>fmt(r.liters)],['Suma (€)',r=>fmt(r.gross)],
      ]}/></details>
      <Button variant="contained" disabled={busy||preview.duplicate} onClick={()=>void upload(true)}>Potvrdiť import</Button>
    </section>}
  </Paper>;
}

const SELECTION='kbm-logbook-selection';
function storedSelection():{vehicleId?:number;year?:number;month?:number} {
  try { return JSON.parse(sessionStorage.getItem(SELECTION)||'{}'); } catch { return {}; }
}
function Workspace({account,signOut}:{account:Account;signOut:()=>void}) {
  const navigate=useNavigate();
  const location=useLocation();
  const [vehicles,setVehicles]=useState<Row[]>([]);
  const [vehicleId,setVehicleId]=useState<number|undefined>(()=>storedSelection().vehicleId);
  const [year,setYear]=useState(()=>storedSelection().year??Number(localDay().slice(0,4)));
  const [month,setMonth]=useState(()=>storedSelection().month??Number(localDay().slice(5,7)));
  // Keep the selected vehicle and month across page reloads (the monthly draft is tied to them).
  useEffect(()=>{
    try { sessionStorage.setItem(SELECTION,JSON.stringify({vehicleId,year,month})); } catch { /* storage unavailable */ }
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
  const [confirm,setConfirm]=useState<{text:string;run?:()=>Promise<unknown>}>();
  const [committing,setCommitting]=useState(false);
  const [search,setSearch]=useState('');
  const [descending,setDescending]=useState(true);
  const [annual,setAnnual]=useState(false);
  const [demoBusy,setDemoBusy]=useState(false);
  const vehicle=vehicles.find(v=>v.id===vehicleId);
  const report=history[month-1];
  const path='/vehicles/'+vehicleId;
  const ready=Boolean(vehicle && report && !busy);
  function reload(message='Zmeny boli uložené.') {
    setEdit(undefined);setUserEdit(undefined);setNotice(message);setRefresh(value=>value+1);
  }
  useEffect(()=>{
    let active=true;
    void api.get('/vehicles').then(({data}:{data:Row[]})=>{
      if (!active) return;
      setVehicles(data);
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
    if (!vehicleId) {setBusy(false);return;}
    setBusy(true);setHistory([]);setDestinations([]);
    void Promise.all([api.get(path+'/reports',{params:{year}}),api.get(path+'/destinations')])
      .then(([reports,places])=>{if(active){setHistory(reports.data);setDestinations(places.data);}})
      .catch(error=>{if(active)setProblem(errorText(error));})
      .finally(()=>{if(active)setBusy(false);});
    return ()=>{active=false;};
  },[path,vehicleId,year,refresh,location.pathname]);
  function rowEdit(kind:'trips'|'fuel',row?:Entry) {
    const fields=kind==='trips'?tripFields:fuelFields;
    const title=kind==='trips'?(row?'Upraviť jazdu':'Pridať jazdu'):(row?'Upraviť tankovanie':'Pridať tankovanie');
    const settings={title,fields,path:path+'/'+kind+(row?'/'+row.id:''),editing:Boolean(row),vehicleId,destinations};
    if (row) {
      const { warnings, ...values } = row;
      setEdit({...settings,initial:{...values,liters:row.purchased,unit_price:''}});
      return;
    }
    const today=localDay();
    const day=today.startsWith(String(report?.month))?today:String(report?.month)+'-01';
    void api.get(path+'/odometer',{params:{day}}).then(({data})=>{
      setEdit({...settings,initial:{day,category:'mesto',start_odo:data.odo,odo:data.odo,vat:data.vat}});
    }).catch(error=>setProblem(errorText(error)));
  }
  function finishedMonth() {
    if (String(year)+'-'+String(month).padStart(2,'0')>=localDay().slice(0,7)) {
      const current=new Date(localDay()+'T12:00:00Z');
      current.setUTCDate(1);current.setUTCMonth(current.getUTCMonth()-1);
      setYear(current.getUTCFullYear());setMonth(current.getUTCMonth()+1);
    }
  }
  function openMonthlyWizard() {
    finishedMonth();
    navigate('/mesacny-navrh');
  }
  // The wizard works only with finished months, also after a reload or a direct link.
  useEffect(()=>{ if(location.pathname==='/mesacny-navrh') finishedMonth(); },[location.pathname,year,month]);
  async function addDemo() {
    setDemoBusy(true);setProblem('');
    try {
      const {data}=await api.post('/demo');
      setVehicleId(data.vehicle_id);setYear(data.year);setMonth(data.month);
      reload('Demo je pripravené. Skúste mesačný návrh od 12 240 do 13 280 km.');
      navigate('/mesacny-navrh');
    } catch(error){setProblem(errorText(error));} finally{setDemoBusy(false);}
  }
  function exportLink(format:string,kind='report',wholeYear=false) {
    return '/vehicle-logbook/api'+path+'/export?year='+year+(wholeYear?'':'&month='+month)+'&format='+format+'&kind='+kind;
  }
  function remove(kind:string,row:Row) {
    setConfirm({text:kind==='trips'?'Odstrániť jazdu? Nadväzujúce výpočty sa môžu zmeniť.':'Natrvalo odstrániť vybraný záznam?',
      run:()=>api.delete(path+'/'+kind+'/'+row.id)});
  }
  const filters=<div className="filters">
    <TextField size="small" select label="Vozidlo" value={vehicleId??''} onChange={e=>setVehicleId(Number(e.target.value))}>
      {!vehicles.length&&<MenuItem value="">Žiadne vozidlo</MenuItem>}
      {vehicles.map(v=><MenuItem value={v.id} key={v.id}>{String(v.registration)}{v.name!==v.registration?' · '+v.name:''}</MenuItem>)}
    </TextField>
    <TextField size="small" select label="Mesiac" value={month} onChange={e=>setMonth(Number(e.target.value))}>
      {Array.from({length:12},(_,index)=><MenuItem value={index+1} key={index}>{monthName(index+1)}</MenuItem>)}
    </TextField>
    <TextField size="small" select label="Rok" value={year} onChange={e=>setYear(Number(e.target.value))}>
      {Array.from(new Set([...Array.from({length:41},(_,index)=>Number(localDay().slice(0,4))+5-index),
        ...vehicles.map(v=>Number(String(v.start_date).slice(0,4))),year])).sort((a,b)=>b-a)
        .map(option=><MenuItem value={option} key={option}>{option}</MenuItem>)}
    </TextField>
  </div>;
  const empty=!vehicle?<Paper variant="outlined" className="empty">Nie je vybrané žiadne dostupné vozidlo.
    {account.admin&&<Button onClick={()=>navigate('/vozidla')}>Pridať vozidlo</Button>}</Paper>
    :busy?<div className="spinner"><CircularProgress size={30} /></div>:null;
  const period=monthName(month)+' '+year;
  const closed=Boolean(report?.closed);
  const issues=report?.rows.flatMap(row=>row.warnings.map(warning=>row.day+' · '+warning))??[];
  const issuesPanel=issues.length>0&&<Alert severity="warning"><details>
    <summary>Kontrola evidencie ({issues.length} upozornení)</summary>
    <ul>{issues.map((issue,index)=><li key={index}>{issue}</li>)}</ul>
  </details></Alert>;
  const status=closed&&<Chip size="small" label="Uzavreté obdobie" color="warning" variant="outlined" />;
  function renderRecords(kind:'trips'|'fuel') {
    const isTrip=kind==='trips';
    const rows=(report?.rows??[]).filter(row=>row.type===(isTrip?'trip':'fuel') &&
      [row.destination,row.origin,row.station,row.note].some(value=>String(value??'').toLocaleLowerCase('sk').includes(search.toLocaleLowerCase('sk'))));
    if(descending) rows.reverse();
    const dayColumn:Column<Entry>=['Dátum',r=><>{r.day}{r.nonworking&&<small className="muted"> voľný deň</small>}</>];
    const fields:Column<Entry>[] = isTrip ? [
      ['Trasa',r=><>{[r.origin,r.destination].filter(Boolean).join(' → ')||'—'}
        {r.estimated&&<small className="muted"> {String(r.source||'Odhad Excelu')}</small>}</>],
      ['Typ',r=>r.category],['Tachometer (km)',r=>fmt(r.start_odo)+' → '+fmt(r.end_odo)],
      ['Km',r=>fmt(r.distance)],['Spotreba (l)',r=>fmt(r.consumed)],
    ]:[
      ['Stanica',r=>r.station||'—'],['Tachometer (km)',r=>fmt(r.odo)],['Litre',r=>fmt(r.purchased)],
      ['Cena/l (€)',r=>fmt(r.unit_price,4)],['S DPH (€)',r=>fmt(r.gross)],['Bez DPH (€)',r=>fmt(r.net)],
      ['DPH (€)',r=>fmt(r.vat_amount)],
    ];
    return <>
      <PageTitle kicker="EVIDENCIA" title={isTrip?'Kniha jázd':'Tankovanie'} description={isTrip?
        'Zapisujte trasy a sledujte nadväznosť tachometra.':'Evidujte doklady, objemy a ceny paliva.'}>
        {ready&&<><Button variant="outlined" href={exportLink('xlsx',kind)}>Stiahnuť XLSX</Button>
          <Button variant="contained" disabled={closed} onClick={()=>rowEdit(kind)}>+ {isTrip?'Pridať jazdu':'Pridať tankovanie'}</Button></>}
      </PageTitle>
      {filters}{empty}{ready&&<><div className="toolbar">
        <TextField size="small" label={isTrip?'Hľadať cieľ alebo trasu':'Hľadať stanicu alebo poznámku'}
          value={search} onChange={e=>setSearch(e.target.value)}/>
        <Button variant="outlined" onClick={()=>setDescending(v=>!v)}>{descending?'Najnovšie prvé':'Najstaršie prvé'}</Button>{status}
      </div>{issuesPanel}<Grid data={rows} columns={[dayColumn,...fields,['Akcie',row=><div className="row-actions">
        <Button size="small" onClick={()=>setConfirm({text:[row.day,row.destination||row.station,
          row.purpose&&('Účel: '+row.purpose),row.note&&('Poznámka: '+row.note),...row.warnings].filter(Boolean).join('\n'),
          })}>Detail</Button>
        {!closed&&<><Button size="small" onClick={()=>rowEdit(kind,row)}>Upraviť</Button>
          <Button size="small" color="error" onClick={()=>remove(kind,row)}>Odstrániť</Button></>}
      </div>]]}/></>}
    </>;
  }
  const max=Math.max(1,...history.map(row=>Number(row.gross)));
  const total=Object.fromEntries(Object.keys(metricLabels).map(key=>[key,history.reduce((sum,r)=>sum+Number(r[key]??0),0)])) as Row;
  total.average=Number(total.distance)?Number(total.consumption)*100/Number(total.distance):0;
  total.gross_per_liter=Number(total.purchased)?Number(total.gross)/Number(total.purchased):0;
  for(const key of ['opening_liters','opening_odo','opening_net']) total[key]=history[0]?.[key];
  for(const key of ['closing_liters','closing_odo','closing_net']) total[key]=history[11]?.[key];
  function monthAction(reopening:boolean) {
    setConfirm({text:reopening?'Otvoriť vybranú uzávierku na úpravy?':'Uzavrieť evidenciu do konca vybraného mesiaca?',
      run:()=>api.post(path+(reopening?'/reopen':'/close'),null,{params:{month:report?.month+'-01'}})});
  }
  return <div className="layout">
    <aside className="sidebar"><Brand /><nav aria-label="Hlavné menu">
      {nav.map(([url,label,icon])=><NavLink key={url} to={url} end={url==='/'}
        onClick={event=>{setSearch('');if(url==='/mesacny-navrh'){event.preventDefault();openMonthlyWizard();}}}><span aria-hidden>{icon}</span>{label}</NavLink>)}
    </nav><div className="profile"><div><b>{account.username}</b><small>{account.admin?'Správca':'Používateľ'}</small></div>
      <Button color="inherit" size="small" href="/">← Portál KBM</Button><Button color="inherit" size="small" onClick={signOut}>Odhlásiť</Button></div></aside>
    <main className="content">
      {problem&&<Alert severity="error" onClose={()=>setProblem('')}>{problem}</Alert>}
      {notice&&<Alert severity="success" onClose={()=>setNotice('')}>{notice}</Alert>}
      <Routes>
        <Route path="/" element={<>
          <PageTitle kicker="FIREMNÁ EVIDENCIA" title="Prehľad" description="Mesačný stav vozidla, náklady a spotreba na jednom mieste.">
            {ready&&<><Button variant="outlined" disabled={closed} onClick={()=>rowEdit('fuel')}>+ Tankovanie</Button>
              <Button variant="contained" onClick={openMonthlyWizard}>Vyplniť mesiac</Button></>}
          </PageTitle>{filters}{empty}
          {account.admin&&!vehicles.length&&<Button variant="contained" disabled={demoBusy} onClick={()=>void addDemo()}>Pridať demo dáta na testovanie</Button>}
          {ready&&<><div className="summary-strip"><strong>{String(vehicle?.registration)} · {period}</strong>{status}</div>
            <Metrics data={report} keys={['distance','calculated','purchased','gross','closing_liters','average','trip_count']}/>
            {issuesPanel}
            <Paper variant="outlined" className="panel"><h2>Mesačné náklady</h2><p>Výdavky na palivo s DPH v roku {year}.</p>
              <div className="chart">{history.map((row,index)=><div key={String(row.month)} className="chart-row">
                <button type="button" onClick={()=>setMonth(index+1)} aria-label={'Vybrať '+monthName(index+1)}>{monthName(index+1)}</button>
                <div className="bar-track"><div className={index+1===month?'active-bar':''} style={{width:100*Number(row.gross)/max+'%'}} /></div>
                <span>{fmt(row.gross)} €</span></div>)}</div>
            </Paper></>}
        </>} />
        <Route path="/mesacny-navrh" element={<>
          <PageTitle kicker="MESAČNÁ EVIDENCIA" title="Mesačný sprievodca" description="Nahrajte bločky a opíšte mesiac v chate. Návrh jázd a tankovaní skontrolujete vľavo pred uložením." />
          {filters}{empty}
          {ready&&<MonthlyWizard key={String(vehicleId)+'-'+year+'-'+month} vehicle={vehicle!}
            month={String(year)+'-'+String(month).padStart(2,'0')} openingOdo={Number(report.opening_odo)}
            destinations={destinations} onSaved={()=>setNotice('Mesačný návrh bol uložený.')} exportUrl={format=>exportLink(format)}/>}
        </>} />
        <Route path="/jazdy" element={renderRecords('trips')} />
        <Route path="/tankovanie" element={renderRecords('fuel')} />
        <Route path="/vykazy" element={<>
          <PageTitle kicker="VÝKAZY" title="Výkazy PHM" description="Mesačné a ročné výkazy spotreby, nákupov a hodnoty paliva.">
            {ready&&<><Button variant="outlined" href={exportLink('pdf','report',annual)}>Stiahnuť PDF</Button>
              <Button variant="contained" href={exportLink('xlsx','report',annual)}>Stiahnuť XLSX</Button></>}
          </PageTitle>{filters}{empty}
          {ready&&<><div className="toolbar"><Button variant={!annual?'contained':'outlined'} onClick={()=>setAnnual(false)}>Mesačný</Button>
            <Button variant={annual?'contained':'outlined'} onClick={()=>setAnnual(true)}>Ročný</Button>
            {!annual&&status}
            {!annual&&account.admin&&<Button variant="outlined" onClick={()=>monthAction(closed)}
              disabled={!closed&&String(report.month)>=localDay().slice(0,7)}>{closed?'Otvoriť uzávierku':'Uzavrieť mesiac'}</Button>}
          </div>
          {(annual?history.some(row=>Number(row.estimated_count)>0):Number(report.estimated_count)>0)&&
            <Alert severity="info">Výkaz obsahuje jazdy z približných podkladov. Ich pôvod je uvedený v exporte.</Alert>}
          <Metrics data={annual?total:report} keys={Object.keys(metricLabels)} />
          {annual?<Grid data={history} columns={[
            ['Mesiac',r=>r.month],['Km',r=>fmt(r.distance)],['Norma (l)',r=>fmt(r.normative)],
            ['Spotreba (l)',r=>fmt(r.consumption)],['Nákup (l)',r=>fmt(r.purchased)],['Nákup (€)',r=>fmt(r.gross)],
            ['Stav (l)',r=>fmt(r.closing_liters)],['Uzávierka',r=>r.closed?'Uzavreté':'Otvorené'],
          ]}/>:<>{issuesPanel}<h2>Detail tankovania</h2><Grid data={report.rows.filter(r=>r.type==='fuel')} columns={[
            ['Dátum',r=>r.day],['Stanica',r=>r.station],['Litre',r=>fmt(r.purchased)],['S DPH (€)',r=>fmt(r.gross)],
            ['Bez DPH (€)',r=>fmt(r.net)],['Stav nádrže (l)',r=>fmt(r.end_liters)],
          ]}/></>}</>}
        </>} />
        <Route path="/vozidla" element={<>
          <PageTitle kicker="ČÍSELNÍKY" title="Vozidlá a destinácie" description="Spravujte vozidlá, ich normy spotreby a obľúbené ciele.">
            {account.admin&&<Button variant="contained" onClick={()=>setEdit({title:'Pridať vozidlo',path:'/vehicles',
              fields:vehicleFields,initial:{start_date:localDay(),initial_odo:0,initial_liters:0,initial_net:0,city_limit:60}})}>+ Pridať vozidlo</Button>}
          </PageTitle>
          <Grid data={vehicles} columns={[
            ['EČV',r=>r.registration],['Vozidlo',r=>r.name],['Palivo',r=>r.fuel_type||'—'],
            ['Kapacita (l)',r=>fmt(r.capacity)],['Norma mesto / mimo',r=>fmt(r.city_rate)+' / '+fmt(r.outside_rate)],
            ['Akcie',r=><div className="row-actions"><Button size="small" onClick={()=>setVehicleId(r.id)}>Vybrať</Button>
              {account.admin&&<Button size="small" onClick={()=>setEdit({title:'Upraviť vozidlo',path:'/vehicles/'+r.id,
                fields:vehicleFields,initial:r,editing:true})}>Upraviť</Button>}</div>],
          ]}/>
          {vehicle&&<><div className="section-heading"><h2>Destinácie · {vehicle.registration}</h2>
            <Button variant="outlined" onClick={()=>setEdit({title:'Pridať cieľ',path:path+'/destinations',
              fields:destinationFields,initial:{}})}>+ Pridať cieľ</Button></div>
          <Grid data={destinations} columns={[
            ['Cieľ',r=>r.name],['Odkiaľ',r=>r.origin||'—'],['Jednosmerne (km)',r=>fmt(r.distance)],['Poznámka',r=>r.note],
            ['Akcie',r=><div className="row-actions"><Button size="small" onClick={()=>setEdit({title:'Upraviť cieľ',
              path:path+'/destinations/'+r.id,fields:destinationFields,initial:r,editing:true})}>Upraviť</Button>
              <Button size="small" color="error" onClick={()=>remove('destinations',r)}>Odstrániť</Button></div>],
          ]}/></>}
        </>} />
        <Route path="/nastavenia" element={<>
          <PageTitle kicker="SPRÁVA" title="Nastavenia" description="Používateľský účet a správa evidencie." />
          <Paper className="panel" variant="outlined"><h2>{account.username}</h2><p>{account.admin?'Správca':'Používateľ'}</p></Paper>
          {account.admin&&<>
            <Paper className="panel" variant="outlined"><h2>Vyskúšať mesačný sprievodca</h2>
              <p>Pridá samostatné demo vozidlo, ukážkové jazdy, destinácie a tankovania. Pre minulý mesiac si môžete vytvoriť vlastný návrh.</p>
              <Button sx={{mt:2}} variant="outlined" disabled={demoBusy} onClick={()=>void addDemo()}>{demoBusy?'Pripravujem demo…':'Pridať / otvoriť demo dáta'}</Button>
            </Paper>
            <ImportPanel done={id=>{setVehicleId(id);reload('Import bol uložený.');navigate('/');}}/>
            <div className="section-heading"><h2>Používatelia</h2><span>Účty a prístup ku Knihe jázd spravuje portál KBM</span></div>
            <Grid data={users} columns={[
              ['Meno',r=>r.name??r.username],['Prihlasovacie meno',r=>r.username],['Rola',r=>r.admin?'Správca':'Používateľ'],['Stav',r=>r.active?'Aktívny':'Neaktívny'],
              ['Akcie',r=><Button size="small" onClick={()=>setUserEdit(r)}>Upraviť</Button>],
            ]}/>
            <h2>História zmien</h2><Grid data={audit} columns={[
              ['Dátum',r=>r.created_at?new Date(String(r.created_at)).toLocaleString('sk-SK',{timeZone:'Europe/Bratislava'}):'—'],
              ['Akcia',r=>r.action],['Používateľ',r=>users.find(u=>u.id===r.user_id)?.username??r.user_id],
              ['Vozidlo',r=>vehicles.find(v=>v.id===r.vehicle_id)?.registration??'—'],
            ]}/>
          </>}
        </>} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </main>
    {edit&&<RecordDialog edit={edit} close={()=>setEdit(undefined)} done={()=>reload()} />}
    {userEdit&&<UserDialog account={userEdit} vehicles={vehicles} close={()=>setUserEdit(undefined)} done={()=>reload()}/>}
    {confirm&&<Dialog open onClose={committing?undefined:()=>setConfirm(undefined)}><DialogTitle>Záznam</DialogTitle>
      <DialogContent><p className="multiline">{confirm.text}</p></DialogContent><DialogActions>
        <Button disabled={committing} onClick={()=>setConfirm(undefined)}>Zavrieť</Button>
        {confirm.run&&<Button variant="contained" disabled={committing} onClick={async()=>{
          setCommitting(true);setProblem('');
          try {await confirm.run?.();setConfirm(undefined);reload();}
          catch(error){setConfirm(undefined);setProblem(errorText(error));} finally{setCommitting(false);}
        }}>Potvrdiť</Button>}
      </DialogActions></Dialog>}
  </div>;
}
function App() {
  const [account,setAccount]=useState<Account|null>();
  useEffect(()=>{
    let active=true;
    void api.get('/me').then(({data})=>{if(active)setAccount(data);}).catch(()=>{if(active)window.location.assign('/?next=vehicle-logbook');});
    const handle=api.interceptors.response.use(value=>value,error=>{
      if(error.response?.status===401)window.location.assign('/?next=vehicle-logbook');
      return Promise.reject(error);
    });
    return ()=>{active=false;api.interceptors.response.eject(handle);};
  },[]);
  const [problem,setProblem]=useState('');
  return account===undefined?<div className="spinner"><CircularProgress /></div>:account?
    <>{problem&&<Alert severity="error" onClose={()=>setProblem('')}>{problem}</Alert>}
      <Workspace account={account} signOut={()=>{
        void fetch('/auth/logout',{method:'POST'}).then(()=>window.location.assign('/')).catch(error=>setProblem(errorText(error)));
      }}/></>:<Login done={setAccount}/>;
}
createRoot(document.getElementById('root')!).render(<ThemeProvider theme={theme}><CssBaseline/>
  <BrowserRouter basename="/vehicle-logbook"><App/></BrowserRouter></ThemeProvider>);

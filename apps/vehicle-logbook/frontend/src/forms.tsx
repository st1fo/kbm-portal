import { useEffect, useState, type FormEvent, type ReactNode } from 'react';
import {
  Alert, Autocomplete, Button, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControlLabel, MenuItem, Stack, Switch, TextField, ToggleButton, ToggleButtonGroup, useMediaQuery,
} from '@mui/material';
import { api, errorText, fmt, type Account, type Row } from './api';

/** Pole formulára; `extra` = zobrazí sa až po „Ďalšie údaje“, `help` = krátka nápoveda pod poľom. */
export type Field = { key: string; label: string; type?: string; required?: boolean; options?: string[];
  help?: string; extra?: boolean; wide?: boolean; section?: string };
export const tripFields: Field[] = [
  {key:'day',label:'Dátum',type:'date',required:true},
  {key:'category',label:'Typ jazdy',options:['mesto','mimo mesto'],required:true},
  {key:'origin',label:'Odkiaľ'},
  {key:'destination',label:'Kam'},
  {key:'start_odo',label:'Tachometer na začiatku (km)',type:'number',help:'Doplní sa podľa predchádzajúcej jazdy.'},
  {key:'end_odo',label:'Tachometer na konci (km)',type:'number',required:true},
  {key:'purpose',label:'Účel jazdy',wide:true},
  {key:'rate',label:'Vlastná spotreba (l/100 km)',type:'number',extra:true,help:'Prázdne = norma vozidla.'},
  {key:'note',label:'Poznámka',extra:true,wide:true},
];
export const fuelFields: Field[] = [
  {key:'day',label:'Dátum',type:'date',required:true},
  {key:'clock',label:'Čas',type:'time'},
  {key:'liters',label:'Natankované (l)',type:'number',required:true},
  {key:'gross',label:'Suma s DPH (€)',type:'number',help:'Celková suma z bločku.'},
  {key:'station',label:'Čerpacia stanica'},
  {key:'odo',label:'Tachometer (km)',type:'number',help:'Ak je na bločku.'},
  {key:'unit_price',label:'Cena za liter s DPH (€)',type:'number',extra:true,help:'Namiesto celkovej sumy.'},
  {key:'vat',label:'DPH (%)',type:'number',required:true,extra:true},
  {key:'measured',label:'Zmeraný stav nádrže po tankovaní (l)',type:'number',extra:true,help:'Iba ak ho poznáte.'},
  {key:'note',label:'Poznámka',extra:true,wide:true},
];
export const vehicleFields: Field[] = [
  {key:'registration',label:'EČV',required:true,section:'Vozidlo'},
  {key:'name',label:'Názov',required:true,help:'Napr. Škoda Octavia'},
  {key:'fuel_type',label:'Palivo',options:['benzín','nafta','LPG','CNG','elektrina','hybrid']},
  {key:'kind',label:'Typ vozidla',options:['osobné','úžitkové','nákladné','iné']},
  {key:'responsible',label:'Zodpovedná osoba',wide:true},
  {key:'city_rate',label:'Spotreba v meste (l/100 km)',type:'number',required:true,section:'Norma spotreby'},
  {key:'outside_rate',label:'Spotreba mimo mesta (l/100 km)',type:'number',required:true},
  {key:'start_date',label:'Začiatok evidencie',type:'date',required:true,section:'Stav na začiatku evidencie'},
  {key:'initial_odo',label:'Tachometer (km)',type:'number',required:true},
  {key:'capacity',label:'Objem nádrže (l)',type:'number',required:true},
  {key:'initial_liters',label:'Palivo v nádrži (l)',type:'number',required:true},
  {key:'initial_net',label:'Hodnota paliva v nádrži bez DPH (€)',type:'number',required:true,wide:true,
    help:'Ak neviete, nechajte 0.'},
];
export const destinationFields: Field[] = [
  {key:'name',label:'Cieľ',required:true,help:'Napr. Košice alebo názov zákazníka'},
  {key:'origin',label:'Odkiaľ'},
  {key:'distance',label:'Vzdialenosť jedným smerom (km)',type:'number',required:true},
  {key:'note',label:'Poznámka',wide:true},
];
export type Edit = { title:string; path:string; fields:Field[]; initial:Row;
  destinations?:Row[]; vehicleId?:number; editing?:boolean; onDelete?:()=>void };

export function RecordDialog({ edit, close, done }: { edit: Edit; close:()=>void; done:(saved?:Row)=>void }) {
  const [data,setData] = useState(edit.initial);
  const [problem,setProblem] = useState('');
  const [busy,setBusy] = useState(false);
  const isTrip = edit.fields === tripFields, isFuel = edit.fields === fuelFields;
  // Ďalšie údaje sú rozbalené, ak už niečo obsahujú.
  const [more,setMore] = useState(() => edit.fields.some(f => f.extra && f.key !== 'vat' && data[f.key] != null && data[f.key] !== ''));
  const phone = useMediaQuery('(max-width:600px)');
  function change(key:string,value:string) {
    setData(previous => ({...previous,[key]:value,
      ...(key === 'unit_price' && value ? {gross:''} : key === 'gross' && value ? {unit_price:''} : {}),
      ...(key === 'category' && value !== previous.category ? {rate:''} : {})}));
    if (key === 'day' && value && edit.vehicleId && !edit.editing) {
      void api.get('/vehicles/'+edit.vehicleId+'/odometer',{params:{day:value}}).then(({data: defaults}) => {
        setData(previous => previous.day !== value ? previous : {...previous,
          ...(isFuel ? {odo:defaults.odo,vat:defaults.vat} : {start_odo:defaults.odo})});
      }).catch(error => setProblem(errorText(error)));
    }
  }
  async function submit(event:FormEvent) {
    event.preventDefault();
    setBusy(true); setProblem('');
    const body = Object.fromEntries(edit.fields.map(field => {
      const value = data[field.key];
      return [field.key,value === '' && (field.type === 'number' || field.type === 'time') ? null : value];
    }));
    try {
      const response = await api.request({ method:edit.editing?'put':'post',url:edit.path,data:body });
      done(response.data);
    } catch (error) {setProblem(errorText(error));}
    finally {setBusy(false);}
  }
  const km = isTrip && data.end_odo !== '' && data.end_odo != null && data.start_odo !== '' && data.start_odo != null
    ? Number(data.end_odo) - Number(data.start_odo) : null;
  const unit = isFuel && Number(data.liters) > 0 && Number(data.gross) > 0 ? Number(data.gross) / Number(data.liters) : null;
  function input(field:Field) {
    const common = {label:field.label,required:field.required,helperText:field.help,
      className:field.wide || field.key === 'note' ? 'wide' : undefined};
    if (field.key === 'category') return <div key={field.key} className="field-toggle">
      <span>{field.label}</span>
      <ToggleButtonGroup exclusive size="small" color="primary" value={data.category ?? 'mesto'}
        onChange={(_,value)=>value && change('category',value)}>
        <ToggleButton value="mesto">V meste</ToggleButton><ToggleButton value="mimo mesto">Mimo mesta</ToggleButton>
      </ToggleButtonGroup></div>;
    if (field.key === 'destination' && edit.destinations?.length) return <Autocomplete key={field.key} freeSolo options={edit.destinations}
      getOptionLabel={option => typeof option === 'string' ? option : String(option.name)}
      inputValue={String(data.destination ?? '')}
      onInputChange={(_,value)=>change('destination',value)}
      onChange={(_,value)=>{
        if (value && typeof value !== 'string') setData(previous=>({...previous,
          destination:value.name,origin:value.origin || previous.origin,category:'mimo mesto',
          ...(previous.start_odo != null && previous.start_odo !== '' ?
            {end_odo:Number(previous.start_odo)+2*Number(value.distance)}:{})}));
      }}
      renderInput={params=><TextField {...params} label={field.label} helperText="Známy cieľ doplní kilometre tam aj späť." />} />;
    return <TextField key={field.key} {...common} type={field.type} select={Boolean(field.options)}
      multiline={field.key==='note'} minRows={field.key==='note'?2:undefined} value={data[field.key] ?? ''}
      onChange={event=>change(field.key,event.target.value)}
      slotProps={{inputLabel:{shrink:true},htmlInput:{step:field.type==='number'?'any':undefined,
        inputMode:field.type==='number'?'decimal':undefined}}}>
      {field.options && !field.required && <MenuItem value="">—</MenuItem>}
      {field.options?.map(option=><MenuItem key={option} value={option}>{option}</MenuItem>)}
      {field.options && data[field.key] && !field.options.includes(String(data[field.key])) &&
        <MenuItem value={String(data[field.key])}>{String(data[field.key])}</MenuItem>}
    </TextField>;
  }
  const main = edit.fields.filter(f => !f.extra), extra = edit.fields.filter(f => f.extra);
  return <Dialog open onClose={busy?undefined:close} fullWidth maxWidth="sm" fullScreen={phone}>
    <form onSubmit={submit} className="record-form">
      <DialogTitle>{edit.title}</DialogTitle>
      <DialogContent>
        {problem && <Alert severity="error" sx={{mb:2}}>{problem}</Alert>}
        <div className="form-grid">
          {main.map(field => <FieldBlock key={field.key} field={field}>{input(field)}</FieldBlock>)}
          {km != null && <p className="wide form-sum">{km > 0 ? <>Vzdialenosť <b>{fmt(km)} km</b></> : 'Koncový stav musí byť vyšší ako začiatočný.'}</p>}
          {unit != null && <p className="wide form-sum">Cena za liter <b>{fmt(unit,3)} €</b></p>}
          {extra.length > 0 && !more && <Button className="wide more-button" onClick={()=>setMore(true)}>+ Ďalšie údaje</Button>}
          {more && extra.map(field => input(field))}
        </div>
      </DialogContent>
      <DialogActions>
        {edit.onDelete && <Button color="error" onClick={edit.onDelete} disabled={busy} sx={{mr:'auto'}}>Odstrániť</Button>}
        <Button onClick={close} disabled={busy}>Zrušiť</Button>
        <Button type="submit" variant="contained" disabled={busy}>{busy ? 'Ukladám…' : 'Uložiť'}</Button>
      </DialogActions>
    </form>
  </Dialog>;
}

function FieldBlock({ field, children }: { field: Field; children: ReactNode }) {
  return <>{field.section && <h3 className="form-section wide">{field.section}</h3>}{children}</>;
}

export function UserDialog({ account, vehicles, close, done }: {account:Account; vehicles:Row[];close:()=>void;done:()=>void}) {
  const [active,setActive] = useState(account.active ?? true);
  const [access,setAccess] = useState(account.vehicle_ids ?? []);
  const [problem,setProblem] = useState('');
  const [busy,setBusy] = useState(false);
  async function submit(event:FormEvent) {
    event.preventDefault(); setBusy(true); setProblem('');
    try {
      await api.put('/users/'+account.id,{username:account.username,password:null,admin:account.admin,active,vehicle_ids:access});
      done();
    } catch(error) {setProblem(errorText(error));} finally {setBusy(false);}
  }
  return <Dialog open onClose={busy?undefined:close} fullWidth maxWidth="sm"><form onSubmit={submit}>
    <DialogTitle>{account.name ?? account.username}</DialogTitle>
    <DialogContent><Stack spacing={2} sx={{pt:1}}>
      {problem&&<Alert severity="error">{problem}</Alert>}
      {account.admin
        ? <Alert severity="info">Správca vidí všetky vozidlá.</Alert>
        : <Autocomplete multiple options={vehicles} getOptionLabel={v=>String(v.registration)+' · '+String(v.name)}
          value={vehicles.filter(v=>access.includes(Number(v.id)))} onChange={(_,values)=>setAccess(values.map(v=>Number(v.id)))}
          renderInput={params=><TextField {...params} label="Vozidlá, ku ktorým má prístup" />} />}
      <FormControlLabel control={<Switch checked={active} onChange={e=>setActive(e.target.checked)} />} label="Môže používať Knihu jázd" />
    </Stack></DialogContent><DialogActions><Button onClick={close} disabled={busy}>Zrušiť</Button>
      <Button variant="contained" type="submit" disabled={busy}>Uložiť</Button></DialogActions>
  </form></Dialog>;
}

/** Trvalé zmazanie vozidla: ukáže, čo všetko zanikne, a vyžaduje opísať EČV. */
export function DeleteVehicleDialog({ vehicle, close, done }: { vehicle: Row; close: () => void; done: (counts: Row) => void }) {
  const [usage,setUsage] = useState<Row>();
  const [typed,setTyped] = useState('');
  const [problem,setProblem] = useState('');
  const [busy,setBusy] = useState(false);
  useEffect(() => { void api.get('/vehicles/'+vehicle.id+'/usage').then(r => setUsage(r.data)).catch(e => setProblem(errorText(e))); }, [vehicle.id]);
  const plate = String(vehicle.registration);
  const match = typed.trim().toUpperCase() === plate.toUpperCase();
  async function remove() {
    setBusy(true); setProblem('');
    try { const { data } = await api.delete('/vehicles/'+vehicle.id,{data:{registration:typed.trim()}}); done(data.counts); }
    catch (error) { setProblem(errorText(error)); } finally { setBusy(false); }
  }
  const items: [string,string][] = [['trips','jázd'],['fuel','tankovaní'],['destinations','uložených cieľov'],
    ['closings','uzavretých mesiacov'],['drafts','mesačných návrhov'],['assistant','rozpracovaných mesiacov v sprievodcovi (aj s bločkami)']];
  return <Dialog open onClose={busy?undefined:close} fullWidth maxWidth="xs">
    <DialogTitle>Odstrániť vozidlo {plate}?</DialogTitle>
    <DialogContent>
      {problem && <Alert severity="error" sx={{mb:2}}>{problem}</Alert>}
      <p>Natrvalo sa zmaže vozidlo <b>{String(vehicle.name)}</b> a všetko k nemu:</p>
      {usage ? <ul className="usage-list">{items.filter(([key]) => Number(usage[key]) > 0).map(([key,label]) => <li key={key}><b>{fmt(usage[key])}</b> {label}</li>)}
        <li>história zmien vozidla a prístupy používateľov</li></ul> : <p className="muted">Načítavam…</p>}
      <Alert severity="warning" sx={{my:2}}>Túto akciu nemožno vrátiť. Ak výkazy ešte potrebujete, najprv si ich stiahnite.</Alert>
      <TextField fullWidth autoFocus label={'Pre potvrdenie napíšte EČV ' + plate} value={typed} onChange={e => setTyped(e.target.value)}
        slotProps={{htmlInput:{autoCapitalize:'characters',autoComplete:'off'}}} />
    </DialogContent>
    <DialogActions><Button onClick={close} disabled={busy}>Zrušiť</Button>
      <Button color="error" variant="contained" disabled={!match || busy} onClick={() => void remove()}>{busy ? 'Mažem…' : 'Odstrániť natrvalo'}</Button>
    </DialogActions>
  </Dialog>;
}

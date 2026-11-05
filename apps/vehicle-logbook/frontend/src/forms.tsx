import { useState, type FormEvent } from 'react';
import {
  Alert, Autocomplete, Button, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControlLabel, MenuItem, Stack, Switch, TextField,
} from '@mui/material';
import { api, errorText, type Account, type Row } from './api';

export type Field = { key: string; label: string; type?: string; required?: boolean; options?: string[] };
export const tripFields: Field[] = [
  {key:'day',label:'Dátum',type:'date',required:true},
  {key:'category',label:'Typ jazdy',options:['mesto','mimo mesto'],required:true},
  {key:'origin',label:'Odkiaľ'},
  {key:'destination',label:'Cieľ'},
  {key:'start_odo',label:'Počiatočný tachometer (km)',type:'number'},
  {key:'end_odo',label:'Koncový tachometer (km)',type:'number',required:true},
  {key:'purpose',label:'Účel jazdy'},
  {key:'rate',label:'Vlastná sadzba (l/100 km)',type:'number'},
  {key:'note',label:'Poznámka'},
];
export const fuelFields: Field[] = [
  {key:'day',label:'Dátum',type:'date',required:true},
  {key:'clock',label:'Čas',type:'time'},
  {key:'odo',label:'Tachometer (km, ak je na doklade)',type:'number'},
  {key:'liters',label:'Natankované (l)',type:'number',required:true},
  {key:'gross',label:'Celková cena s DPH (€)',type:'number'},
  {key:'unit_price',label:'Alebo cena s DPH za liter (€)',type:'number'},
  {key:'vat',label:'Sadzba DPH (%)',type:'number',required:true},
  {key:'station',label:'Čerpacia stanica'},
  {key:'measured',label:'Meraný stav nádrže po tankovaní (l)',type:'number'},
  {key:'note',label:'Poznámka'},
];
export const vehicleFields: Field[] = [
  {key:'name',label:'Názov vozidla',required:true},
  {key:'registration',label:'EČV',required:true},
  {key:'kind',label:'Typ vozidla'},
  {key:'fuel_type',label:'Palivo'},
  {key:'responsible',label:'Zodpovedná osoba'},
  {key:'start_date',label:'Začiatok evidencie',type:'date',required:true},
  {key:'initial_odo',label:'Počiatočný tachometer (km)',type:'number',required:true},
  {key:'capacity',label:'Kapacita nádrže (l)',type:'number',required:true},
  {key:'initial_liters',label:'Počiatočný stav (l)',type:'number',required:true},
  {key:'initial_net',label:'Počiatočná hodnota bez DPH (€)',type:'number',required:true},
  {key:'city_rate',label:'Norma v meste (l/100 km)',type:'number',required:true},
  {key:'outside_rate',label:'Norma mimo mesta (l/100 km)',type:'number',required:true},
  {key:'city_limit',label:'Historický limit km v meste',type:'number',required:true},
];
export const destinationFields: Field[] = [
  {key:'name',label:'Názov cieľa',required:true},
  {key:'origin',label:'Odkiaľ'},
  {key:'distance',label:'Jednosmerná vzdialenosť (km)',type:'number',required:true},
  {key:'note',label:'Poznámka'},
];
export type Edit = { title:string; path:string; fields:Field[]; initial:Row;
  destinations?:Row[]; vehicleId?:number; editing?:boolean };

export function RecordDialog({ edit, close, done }: { edit: Edit; close:()=>void; done:()=>void }) {
  const [data,setData] = useState(edit.initial);
  const [problem,setProblem] = useState('');
  const [busy,setBusy] = useState(false);
  function change(key:string,value:string) {
    setData(previous => ({...previous,[key]:value,
      ...(key === 'unit_price' && value ? {gross:''} : key === 'gross' && value ? {unit_price:''} : {}),
      ...(key === 'category' && value !== previous.category ? {rate:''} : {})}));
    if (key === 'day' && value && edit.vehicleId && !edit.editing) {
      void api.get('/vehicles/'+edit.vehicleId+'/odometer',{params:{day:value}}).then(({data: defaults}) => {
        setData(previous => previous.day !== value ? previous : {...previous,
          ...(edit.fields === fuelFields ? {odo:defaults.odo,vat:defaults.vat} : {start_odo:defaults.odo})});
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
      await api.request({ method:edit.editing?'put':'post',url:edit.path,data:body });
      done();
    } catch (error) {setProblem(errorText(error));}
    finally {setBusy(false);}
  }
  return <Dialog open onClose={busy?undefined:close} fullWidth maxWidth="sm">
    <form onSubmit={submit}>
      <DialogTitle>{edit.title}</DialogTitle>
      <DialogContent>
        {problem && <Alert severity="error" sx={{mb:2}}>{problem}</Alert>}
        <div className="form-grid">
          {edit.fields.map(field => field.key === 'destination' && edit.destinations?.length ?
            <Autocomplete key={field.key} freeSolo options={edit.destinations}
              getOptionLabel={option => typeof option === 'string' ? option : String(option.name)}
              inputValue={String(data.destination ?? '')}
              onInputChange={(_,value)=>change('destination',value)}
              onChange={(_,value)=>{
                if (value && typeof value !== 'string') setData(previous=>({...previous,
                  destination:value.name,origin:value.origin || previous.origin,category:'mimo mesto',
                  ...(previous.start_odo != null && previous.start_odo !== '' ?
                    {end_odo:Number(previous.start_odo)+2*Number(value.distance)}:{})}));
              }}
              renderInput={params=><TextField {...params} label={field.label} />} />
            : <TextField key={field.key} className={field.key === 'note' ? 'wide' : undefined}
                label={field.label} type={field.type} select={Boolean(field.options)}
                multiline={field.key==='note'} minRows={field.key==='note'?2:undefined}
                required={field.required} value={data[field.key] ?? ''}
                onChange={event=>change(field.key,event.target.value)}
                slotProps={{inputLabel:{shrink:true},htmlInput:{step:field.type==='number'?'any':undefined}}}>
                {field.options?.map(option=><MenuItem key={option} value={option}>{option}</MenuItem>)}
              </TextField>)}
        </div>
        {edit.fields === tripFields && <Alert severity="info" sx={{mt:2}}>Počiatočný stav možno ponechať prázdny. Pri zmene koncového tachometra sa prepočíta nadväzujúca jazda.</Alert>}
        {edit.fields === fuelFields && <Alert severity="info" sx={{mt:2}}>Použite celkovú sumu z dokladu alebo jednotkovú cenu. Meraný stav zadávajte iba vtedy, keď ho poznáte.</Alert>}
      </DialogContent>
      <DialogActions><Button onClick={close} disabled={busy}>Zrušiť</Button><Button type="submit" variant="contained" disabled={busy}>Uložiť</Button></DialogActions>
    </form>
  </Dialog>;
}

export function UserDialog({ account, vehicles, close, done }: {account:Account; vehicles:Row[];close:()=>void;done:()=>void}) {
  const [username,setUsername] = useState(account.username);
  const password = ''; 
  const [admin,setAdmin] = useState(account.admin);
  const [active,setActive] = useState(account.active ?? true);
  const [access,setAccess] = useState(account.vehicle_ids ?? []);
  const [problem,setProblem] = useState('');
  const [busy,setBusy] = useState(false);
  async function submit(event:FormEvent) {
    event.preventDefault(); setBusy(true); setProblem('');
    try {
      await api.request({method:account.id?'put':'post',url:account.id?'/users/'+account.id:'/users',
        data:{username,password:password||null,admin,active,vehicle_ids:access}});
      done();
    } catch(error) {setProblem(errorText(error));} finally {setBusy(false);}
  }
  return <Dialog open onClose={busy?undefined:close} fullWidth maxWidth="sm"><form onSubmit={submit}>
    <DialogTitle>{account.id?'Upraviť používateľa':'Pridať používateľa'}</DialogTitle>
    <DialogContent><Stack spacing={2} sx={{pt:1}}>
      {problem&&<Alert severity="error">{problem}</Alert>}
      <TextField disabled label="Prihlasovacie meno" value={username} onChange={e=>setUsername(e.target.value)} required />
      <p>Heslo si zmeníte v nastaveniach portálu KBM.</p>
      <FormControlLabel control={<Switch disabled checked={admin} onChange={e=>setAdmin(e.target.checked)} />} label="Správca" />
      <FormControlLabel control={<Switch checked={active} onChange={e=>setActive(e.target.checked)} />} label="Aktívny účet" />
      {!admin&&<Autocomplete multiple options={vehicles} getOptionLabel={v=>String(v.registration)}
        value={vehicles.filter(v=>access.includes(Number(v.id)))} onChange={(_,values)=>setAccess(values.map(v=>Number(v.id)))}
        renderInput={params=><TextField {...params} label="Prístup k vozidlám" />} />}
    </Stack></DialogContent><DialogActions><Button onClick={close} disabled={busy}>Zrušiť</Button>
      <Button variant="contained" type="submit" disabled={busy}>Uložiť</Button></DialogActions>
  </form></Dialog>;
}

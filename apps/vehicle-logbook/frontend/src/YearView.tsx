import { useEffect, useState } from 'react';
import { Alert, CircularProgress, Paper } from '@mui/material';
import { api, errorText, fmt, monthName, plural, type Report, type Row } from './api';
import { Icon } from './icons';

/** Ročný pohľad na Prehľade: graf po mesiacoch, stav knihy vybraného vozidla a (správca) porovnanie vozidiel. */
type Metric = 'distance'|'gross'|'average'|'gross_per_liter';
const METRICS: Record<Metric,{tab:string;title:string;unit:string;places:number}> = {
  distance:{tab:'Km',title:'Najazdené',unit:'km',places:0},
  gross:{tab:'Náklady',title:'Náklady na palivo',unit:'€',places:0},
  average:{tab:'Spotreba',title:'Spotreba',unit:'l/100 km',places:1},
  gross_per_liter:{tab:'€/l',title:'Cena za liter',unit:'€/l',places:3},
};
const norm100 = (r:Row) => Number(r.distance) ? Number(r.normative)*100/Number(r.distance) : 0;
const above = (value:unknown, norm:unknown) => Number(norm)>0 && Number(value)>Number(norm);

export function YearView({ year, month, history, vehicle, admin, thisMonth, refresh, onMonth, onVehicle }:{
  year:number; month:number; history:Report[]; vehicle:Row; admin:boolean; thisMonth:string; refresh:number;
  onMonth:(month:number)=>void; onVehicle:(id:number)=>void }) {
  const [metric,setMetric]=useState<Metric>('distance');
  const [overview,setOverview]=useState<{vehicles:Row[];total:Row}>();
  const [problem,setProblem]=useState('');
  useEffect(()=>{
    if(!admin) return;
    let active=true; setOverview(undefined); setProblem('');
    void api.get('/overview',{params:{year}}).then(({data})=>{if(active)setOverview(data);})
      .catch(error=>{if(active)setProblem(errorText(error));});
    return ()=>{active=false;};
  },[admin,year,refresh]);

  const info=METRICS[metric];
  const max=Math.max(1e-9,...history.map(r=>Math.max(Number(r[metric]),metric==='average'?norm100(r):0)));
  const started=String(vehicle.start_date??'').slice(0,7);
  const trips=history.reduce((s,r)=>s+Number(r.trip_count),0), estimated=history.reduce((s,r)=>s+Number(r.estimated_count),0);
  const warnings=history.reduce((s,r)=>s+r.rows.reduce((n,row)=>n+row.warnings.length,0),0);
  const state=(r:Report)=>r.closed?'closed':String(r.month)>thisMonth||String(r.month)<started?'none':String(r.month)===thisMonth?'running':'open';
  const open=history.filter(r=>state(r)==='open').length;

  return <>
    <div className="two-col">
      <Paper variant="outlined" className="panel"><div className="section-heading"><h2>{info.title} {year}</h2>
        <div className="segmented small" role="tablist">{(Object.keys(METRICS) as Metric[]).map(key=>
          <button type="button" role="tab" key={key} aria-selected={metric===key} className={metric===key?'on':''} onClick={()=>setMetric(key)}>{METRICS[key].tab}</button>)}</div></div>
        <div className="chart">{history.map((row,index)=>{
          const value=Number(row[metric]), norm=metric==='average'?norm100(row):0;
          return <button type="button" key={String(row.month)} className={'chart-row wide-value'+(index+1===month?' on':'')}
            onClick={()=>onMonth(index+1)} aria-label={'Vybrať '+monthName(index+1)}
            title={norm?`Norma ${fmt(norm,1)} l/100 km`:undefined}>
            <span>{monthName(index+1).slice(0,3)}</span>
            <span className="bar-track"><i className={above(value,norm)?'over':''} style={{width:100*value/max+'%'}} />
              {norm>0&&<b className="norm-mark" style={{left:`calc(${100*norm/max}% - 1px)`}}/>}</span>
            <span className="num">{value?fmt(value,info.places)+' '+info.unit:'–'}</span></button>;})}</div>
        {metric==='average'&&<p className="muted chart-note"><b className="norm-mark inline"/> norma vozidla · <span className="text-warn">oranžová</span> = nad normou</p>}
      </Paper>
      <Paper variant="outlined" className="panel"><h2>Stav knihy {year}</h2>
        <div className="month-status">{history.map((row,index)=><button type="button" key={String(row.month)} className={state(row)+(index+1===month?' on':'')}
          onClick={()=>onMonth(index+1)} title={{closed:'Uzavretý',open:'Otvorený – treba dokončiť a uzavrieť',running:'Prebiehajúci mesiac',none:'Bez evidencie'}[state(row)]}>
          {state(row)==='closed'&&<Icon name="lock" size={12}/>}{monthName(index+1).slice(0,3)}</button>)}</div>
        <dl className="status-list">
          <div><dt>Neuzavreté skončené mesiace</dt><dd className={open?'text-warn':''}>{open}</dd></div>
          <div><dt>Odhadnuté jazdy</dt><dd>{trips?`${fmt(100*estimated/trips,0)} %`:'–'} <small className="muted">{fmt(estimated)} z {fmt(trips)} {plural(trips,'jazdy','jázd','jázd')}</small></dd></div>
          <div><dt>Upozornenia v knihe</dt><dd className={warnings?'text-warn':''}>{warnings}</dd></div>
        </dl>
      </Paper>
    </div>
    {admin&&<Paper variant="outlined" className="panel"><h2>Porovnanie vozidiel {year}</h2>
      {problem?<Alert severity="error">{problem}</Alert>:!overview?<CircularProgress size={24}/>:
      <div className="table-wrap"><table className="records fleet">
        <thead><tr><th>Vozidlo</th><th className="num">Km</th><th className="num">Náklady</th><th className="num wide-only">Litre</th>
          <th className="num">l/100 km</th><th className="num wide-only">€/km</th><th className="num wide-only">Tankovania</th></tr></thead>
        <tbody>{overview.vehicles.map(v=><tr key={String(v.id)} role="button" tabIndex={0} className={v.id===vehicle.id?'selected':''}
          onClick={()=>onVehicle(Number(v.id))} onKeyDown={e=>{if(e.key==='Enter')onVehicle(Number(v.id));}}>
          <td><b>{String(v.registration)}</b><small className="muted block">{String(v.name)}</small></td>{figures(v)}</tr>)}</tbody>
        <tfoot><tr><td><b>Spolu</b></td>{figures(overview.total)}</tr></tfoot>
      </table></div>}
    </Paper>}
  </>;
}

function figures(r:Row) {
  const km=Number(r.distance);
  return <><td className="num">{fmt(r.distance,0)}</td><td className="num nowrap">{fmt(r.gross,0)} €</td><td className="num wide-only">{fmt(r.purchased,0)}</td>
    <td className="num nowrap">{km?<><span className={above(r.average,r.norm)?'text-warn':''}>{fmt(r.average,1)}</span>
      <small className="muted block">norma {fmt(r.norm,1)}</small></>:'–'}</td>
    <td className="num wide-only">{km?fmt(r.per_km,3):'–'}</td><td className="num wide-only">{fmt(r.refuels)}</td></>;
}

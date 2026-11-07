export type User={id:number;name:string;email:string;role:string;active:boolean;daily_hours:number;groups?:string[];pages?:string[]};
export type Kind={id:number;name:string;icon:string;color:string;description:string;requires_approval:boolean;active:boolean};
export type Entry={id:number;user_id:number;date:string;type_id:number;start:string|null;end:string|null;note:string;request_id:number|null};
export type Leave={id:number;user_id:number;type_id:number;start_date:string;end_date:string;status:string;note:string;reason:string;reviewed_by:number|null;reviewed_at:string|null;hours?:number|null};
/** Počet hodín po slovensky (1,5). */
export const hrs=(h:number)=>String(Math.round(h*100)/100).replace('.',',');
export const getToken=()=>'portal-session';
export async function api<T=any>(path:string,method='GET',data?:unknown):Promise<T>{
 const r=await fetch('/workplan/api'+path,{method,headers:data?{'Content-Type':'application/json'}:{},body:data?JSON.stringify(data):undefined});
 if(!r.ok){let p;try{p=await r.json()}catch{p={detail:'Server je nedostupný.'}}if(r.status===401&&path!='/auth/login'){window.location.assign('/?next=workplan')}throw new Error(typeof p.detail==='string'?p.detail:'Skontrolujte dátumy, časy a povinné polia.')};return r.json();
}
export const localDay=()=>new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Bratislava'}).format(new Date());
export const dateLabel=(d:string)=>new Date(d+'T12:00:00').toLocaleDateString('sk-SK');

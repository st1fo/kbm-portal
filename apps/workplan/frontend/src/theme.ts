import {useEffect,useState} from 'react';
/** Vzhľad zdieľaný s portálom a druhou aplikáciou (rovnaký origin, kľúč kbm-theme). Pred vykreslením ho nastaví public/theme-init.js. */
export type ThemePref='light'|'dark'|'auto';
export type ThemeMode='light'|'dark';
const KEY='kbm-theme';
export const themeLabels:Record<ThemePref,string>={light:'svetlý',dark:'tmavý',auto:'automaticky'};
export const nextTheme:Record<ThemePref,ThemePref>={light:'dark',dark:'auto',auto:'light'};
const valid=(v:unknown):v is ThemePref=>v==='light'||v==='dark'||v==='auto';
function readTheme():ThemePref{try{const v=localStorage.getItem(KEY);if(valid(v))return v}catch{/* úložisko nedostupné */}return 'auto'}
function darkQuery():MediaQueryList|null{try{return window.matchMedia('(prefers-color-scheme: dark)')}catch{return null}}
function applyMode(mode:ThemeMode){
  const root=document.documentElement;root.dataset.theme=mode;root.style.colorScheme=mode;
  const meta=document.querySelector('meta[name="theme-color"]');const color=meta?.getAttribute('data-'+mode);if(meta&&color)meta.setAttribute('content',color);
}
/** Vráti zvolený vzhľad, funkciu na jeho zmenu a skutočne použitý režim (auto podľa systému). */
export function useTheme():[ThemePref,(p:ThemePref)=>void,ThemeMode]{
  const [pref,setPref]=useState<ThemePref>(readTheme);
  const [systemDark,setSystemDark]=useState(()=>Boolean(darkQuery()?.matches));
  useEffect(()=>{
    const q=darkQuery();const onChange=(e:MediaQueryListEvent)=>setSystemDark(e.matches);
    q?.addEventListener?.('change',onChange);
    const onStorage=(e:StorageEvent)=>{if(e.key===KEY)setPref(valid(e.newValue)?e.newValue:'auto')};
    window.addEventListener('storage',onStorage);
    return ()=>{q?.removeEventListener?.('change',onChange);window.removeEventListener('storage',onStorage)};
  },[]);
  const mode:ThemeMode=pref==='auto'?(systemDark?'dark':'light'):pref;
  useEffect(()=>applyMode(mode),[mode]);
  return [pref,p=>{try{localStorage.setItem(KEY,p)}catch{/* úložisko nedostupné */}setPref(p)},mode];
}

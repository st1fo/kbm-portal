/* Vzhľad KBM: svetlý / tmavý / automaticky (podľa systému). Spoločný kľúč kbm-theme pre portál aj aplikácie.
   Načítava sa synchrónne v <head>, aby sa vzhľad nastavil ešte pred vykreslením. */
(function(){
var KEY='kbm-theme',LABEL={light:'svetlý',dark:'tmavý',auto:'automaticky'},NEXT={light:'dark',dark:'auto',auto:'light'};
var ICON={light:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M6.3 17.7l-1.4 1.4M19.1 4.9l-1.4 1.4"/>',
  dark:'<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',auto:'<rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/>'};
function valid(v){return v==='light'||v==='dark'||v==='auto'}
function read(){try{var v=localStorage.getItem(KEY);if(valid(v))return v}catch(e){}return 'auto'}
var query=null;try{query=window.matchMedia('(prefers-color-scheme: dark)')}catch(e){}
var pref=read();
function apply(){
  var t=pref==='auto'?(query&&query.matches?'dark':'light'):pref,root=document.documentElement;
  root.setAttribute('data-theme',t);root.style.colorScheme=t;
  var meta=document.querySelector('meta[name="theme-color"]');if(meta&&meta.getAttribute('data-'+t))meta.setAttribute('content',meta.getAttribute('data-'+t));
  var b=document.getElementById('theme-toggle');
  if(b){b.setAttribute('aria-label','Vzhľad: '+LABEL[pref]);b.title='Vzhľad: '+LABEL[pref]+' (kliknutím prepnete na '+LABEL[NEXT[pref]]+')';b.querySelector('svg').innerHTML=ICON[pref];}
}
apply();
if(query){var onChange=function(){if(pref==='auto')apply()};if(query.addEventListener)query.addEventListener('change',onChange);else if(query.addListener)query.addListener(onChange);}
window.addEventListener('storage',function(e){if(e.key===KEY){pref=valid(e.newValue)?e.newValue:'auto';apply();}});
document.addEventListener('DOMContentLoaded',function(){
  var b=document.getElementById('theme-toggle');if(!b)return;
  b.addEventListener('click',function(){pref=NEXT[pref];try{localStorage.setItem(KEY,pref)}catch(e){}apply();});
  apply();
});
})();

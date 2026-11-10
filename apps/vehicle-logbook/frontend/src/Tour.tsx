import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { Button } from '@mui/material';

/** Návod pri prvom otvorení; rovnaká logika ako vo Workplane (apps/workplan/frontend/src/Tour.tsx).
 *  Krok s `target` zvýrazní prvok `[data-tour=…]`; `page` prepne stránku Knihy jázd. */
export type TourStep = { title: string; text: string; target?: string; page?: string };

const doneKey = (user: string) => 'kbm-logbook-tour-done:' + user;
export function tourSeen(user: string) { try { return localStorage.getItem(doneKey(user)) === '1'; } catch { return true; } }
function markSeen(user: string) { try { localStorage.setItem(doneKey(user), '1'); } catch { /* bez úložiska sa návod ukáže znova */ } }

/** Na telefóne sú Vozidlá, Správa aj návod v ponuke účtu, preto tam kroky ukazujú na avatar. */
export function tourSteps(isAdmin: boolean, phone: boolean): TourStep[] {
  const menu = (url: string) => phone ? 'account' : 'nav' + url;
  return [
    { title: 'Vitajte v Knihe jázd', text: 'Za minútu vám ukážeme, ako to funguje. Návod môžete kedykoľvek preskočiť a neskôr ho spustiť znova.' },
    { page: '/', target: 'context', title: 'Vozidlo a mesiac', text: 'Hore si vyberiete vozidlo a mesiac. Všetko, čo vidíte nižšie, patrí k tomuto výberu.' },
    { page: '/', target: 'quick', title: 'Rýchle akcie', text: 'Odtiaľto pridáte jednu jazdu, tankovanie z bločku alebo necháte asistenta vyplniť celý mesiac.' },
    { page: '/', target: 'stats', title: 'Prehľad mesiaca', text: 'Najazdené kilometre, spotreba, tankovanie a stav nádrže na konci mesiaca. Ak niečo nesedí, upozornenie sa ukáže nad nimi.' },
    { page: '/jazdy', target: 'nav/jazdy', title: 'Jazdy', text: 'Každá cesta autom je jeden riadok. Tachometer nadväzuje sám a známy cieľ doplní kilometre tam aj späť.' },
    { page: '/tankovanie', target: 'nav/tankovanie', title: 'Tankovanie', text: isAdmin
      ? 'Tankovania zapíšete z bločku. Ako správca môžete nahrať aj súhrnnú faktúru z tankovacích kariet.'
      : 'Tankovania zapíšete z bločku: dátum, litre a sumu. Tachometer vyplňte, iba ak je na bločku.' },
    { page: '/mesacny-navrh', target: 'nav/mesacny-navrh', title: 'Vyplniť mesiac', text: 'Po skončení mesiaca nahráte bločky a napíšete stav tachometra a kde ste približne jazdili. Asistent navrhne jazdy na celý mesiac, vy ich skontrolujete a schválite.' },
    { page: '/vykazy', target: 'nav/vykazy', title: 'Výkazy', text: 'Súhrn mesiaca aj celého roka. Výkaz stiahnete ako PDF alebo Excel.' },
    { page: '/vozidla', target: menu('/vozidla'), title: 'Vozidlá a časté ciele', text: 'Údaje o vozidlách a zoznam častých cieľov so vzdialenosťami. Z nich sa pri zápise jazdy doplnia kilometre.' },
    ...(isAdmin ? [{ page: '/sprava', target: menu('/sprava'), title: 'Správa', text: 'Kto vidí ktoré vozidlo, import zo starého Excelu a ukážkové vozidlo na vyskúšanie. Účty pridávate v portáli KBM.' }] : []),
    { target: 'portal', title: 'Všetky aplikácie', text: 'Týmto tlačidlom sa vrátite na hlavnú stránku portálu, napríklad do Workplanu.' },
    { target: phone ? 'account' : 'help', title: 'Hotovo', text: phone ? 'Návod spustíte znova z ponuky účtu.' : 'Návod spustíte znova týmto tlačidlom s otáznikom.' },
  ];
}

type Box = { top: number; left: number; width: number; height: number };

export function Tour({ user, steps, setPage, onClose }: { user: string; steps: TourStep[]; setPage: (p: string) => void; onClose: () => void }) {
  const [i, setI] = useState(0);
  const [box, setBox] = useState<Box | null>(null);
  const [ready, setReady] = useState(false);
  const card = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const step = steps[i];
  const last = i === steps.length - 1;
  const finish = useCallback(() => { markSeen(user); onClose(); }, [user, onClose]);

  // prepnúť stránku a počkať, kým sa cieľový prvok vykreslí
  useEffect(() => {
    if (step.page) setPage(step.page);
    setReady(false); setBox(null);
    if (!step.target) { setReady(true); return; }
    let tries = 0, raf = 0;
    const find = () => {
      const el = document.querySelector<HTMLElement>(`[data-tour="${step.target}"]`);
      if (el && el.offsetParent !== null) {
        el.scrollIntoView({ block: 'center', inline: 'center', behavior: 'instant' as ScrollBehavior });
        measure(); setReady(true); return;
      }
      if (++tries < 90) raf = requestAnimationFrame(find);
      else if (i < steps.length - 1) setI(i + 1);   // prvok chýba (napr. žiadne tímy), krok preskočíme
      else setReady(true);
    };
    raf = requestAnimationFrame(find);
    return () => cancelAnimationFrame(raf);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [i]);

  const measure = useCallback(() => {
    if (!step.target) { setBox(null); return; }
    const el = document.querySelector<HTMLElement>(`[data-tour="${step.target}"]`);
    if (!el) { setBox(null); return; }
    const r = el.getBoundingClientRect(), pad = 6;
    setBox({ top: r.top - pad, left: r.left - pad, width: r.width + 2 * pad, height: r.height + 2 * pad });
  }, [step.target]);

  useEffect(() => {
    window.addEventListener('resize', measure); window.addEventListener('scroll', measure, true);
    return () => { window.removeEventListener('resize', measure); window.removeEventListener('scroll', measure, true); };
  }, [measure]);

  // umiestnenie karty: pod prvok, ak sa nezmestí tak nad neho; na mobile vždy dole
  useLayoutEffect(() => {
    const c = card.current; if (!c) return;
    const w = c.offsetWidth, h = c.offsetHeight, vw = window.innerWidth, vh = window.innerHeight, gap = 14;
    if (!box || vw < 640) { setPos(null); return; }
    let top = box.top + box.height + gap;
    if (top + h > vh - 12) top = box.top - h - gap;
    if (top < 12) top = Math.min(vh - h - 12, Math.max(12, box.top + box.height / 2 - h / 2));
    const left = Math.min(vw - w - 16, Math.max(16, box.left + box.width / 2 - w / 2));
    setPos({ top, left });
  }, [box, ready, i]);

  useEffect(() => { if (ready) card.current?.querySelector<HTMLButtonElement>('.tour-next')?.focus(); }, [ready, i]);
  useEffect(() => {
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') finish();
      else if (e.key === 'ArrowRight') { if (last) finish(); else setI(i + 1); }
      else if (e.key === 'ArrowLeft' && i > 0) setI(i - 1);
    };
    window.addEventListener('keydown', key); return () => window.removeEventListener('keydown', key);
  }, [i, last, finish]);

  const centered = !box || !step.target;
  const narrow = window.innerWidth < 640;   // telefón: karta ako pás dole/hore; inak pri prvku (šírka karty sa meria bez triedy „sheet“)
  const sheetTop = !!box && box.top + box.height / 2 > window.innerHeight / 2;
  return <div className="tour" role="dialog" aria-modal="true" aria-labelledby="tour-title">
    <div className={'tour-shade' + (centered ? ' full' : '')} />
    {box && !centered && <div className="tour-spot" style={box} />}
    <div ref={card} className={'tour-card' + (centered ? ' centered' : !narrow ? '' : sheetTop ? ' sheet top' : ' sheet')} style={pos && !centered && !narrow ? pos : undefined} hidden={!ready}>
      <div className="tour-progress" aria-hidden="true">{steps.map((_, k) => <i key={k} className={k <= i ? 'on' : ''} />)}</div>
      <h2 id="tour-title">{step.title}</h2>
      <p>{step.text}</p>
      <div className="tour-buttons">
        {!last && <button type="button" className="tour-skip" onClick={finish}>Preskočiť návod</button>}
        <span className="tour-count">{i + 1} / {steps.length}</span>
        {i > 0 && <Button size="small" variant="outlined" onClick={() => setI(i - 1)}>Späť</Button>}
        <Button size="small" variant="contained" className="tour-next" onClick={() => last ? finish() : setI(i + 1)}>{i === 0 ? 'Začať' : last ? 'Rozumiem' : 'Ďalej'}</Button>
      </div>
    </div>
  </div>;
}

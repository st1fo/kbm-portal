import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

/** Krok návodu: ak má `target`, zvýrazní prvok `[data-tour=…]`; `page` prepne stránku Workplanu. */
export type TourStep = { title: string; text: string; target?: string; page?: string };

const doneKey = (uid: number) => 'kbm-workplan-tour-done:' + uid;
export function tourSeen(uid: number) { try { return localStorage.getItem(doneKey(uid)) === '1'; } catch { return true; } }
function markSeen(uid: number) { try { localStorage.setItem(doneKey(uid), '1'); } catch { /* bez úložiska sa návod ukáže znova */ } }

export function tourSteps(isAdmin: boolean): TourStep[] {
  return [
    { title: 'Vitajte vo Workplane', text: 'Za minútu vám ukážeme, ako to funguje. Návod môžete kedykoľvek preskočiť a neskôr ho spustiť znova.' },
    { page: 'calendar', target: 'calendar', title: 'Dochádzka: plán tímu', text: isAdmin
      ? 'Každý riadok je človek, každý stĺpec deň. Ikona ukazuje, kde kto pracuje. Kliknutím na políčko naplánujete deň komukoľvek.'
      : 'Každý riadok je človek, každý stĺpec deň. Ikona ukazuje, kde kto pracuje. Kliknutím na políčko vo svojom riadku si deň naplánujete.' },
    { page: 'calendar', target: 'plan', title: 'Naplánovať deň', text: 'Kancelária, práca z domu, aj viac dní naraz. Dovolenka či PN sa odošle ako žiadosť na schválenie.' },
    { page: 'calendar', target: 'teams', title: 'Výber tímu', text: 'Zobrazí iba ľudí z vybraného tímu. Výber si Workplan zapamätá.' },
    { page: 'journal', target: 'punch', title: 'Denník: príchod a odchod', text: 'Keď prídete do práce, pípnite príchod, pri odchode odchod. Funguje aj bez internetu, pípnutie sa odošle neskôr.' },
    { page: 'journal', target: 'journal-tabs', title: 'Na čom robíte', text: 'V záložke Dnes zapíšete, na čom ste robili. Nástenky sú úlohy na kartách, ktoré preťahujete medzi stĺpcami (aj tímové), Mesiac ukazuje odpracované dni.' },
    { page: 'requests', target: 'nav-requests', title: 'Žiadosti o voľno', text: isAdmin
      ? 'Tu schvaľujete dovolenky a iné neprítomnosti tímu. Nové žiadosti uvidíte aj pri zvončeku.'
      : 'Tu požiadate o dovolenku alebo voľno a uvidíte, či je schválené. O výsledku vám príde správa pri zvončeku.' },
    { page: 'dashboard', target: 'nav-dashboard', title: 'Prehľad', text: 'Kto je dnes v kancelárii, kto doma a aké neprítomnosti sa blížia.' },
    ...(isAdmin ? [{ page: 'admin', target: 'nav-admin', title: 'Administrácia', text: 'Kategórie dochádzky, pracovný čas a sviatky. Účty a tímy spravujete v portáli KBM.' }] : []),
    { target: 'portal', title: 'Všetky aplikácie', text: 'Týmto tlačidlom sa vrátite na hlavnú stránku portálu, napríklad do Knihy jázd.' },
    { target: 'help', title: 'Hotovo', text: 'Návod spustíte znova týmto tlačidlom s otáznikom.' },
  ];
}

type Box = { top: number; left: number; width: number; height: number };

export function Tour({ uid, steps, setPage, onClose }: { uid: number; steps: TourStep[]; setPage: (p: string) => void; onClose: () => void }) {
  const [i, setI] = useState(0);
  const [box, setBox] = useState<Box | null>(null);
  const [ready, setReady] = useState(false);
  const card = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const step = steps[i];
  const last = i === steps.length - 1;
  const finish = useCallback(() => { markSeen(uid); onClose(); }, [uid, onClose]);

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
  return <div className="tour" role="dialog" aria-modal="true" aria-labelledby="tour-title">
    <div className={'tour-shade' + (centered ? ' full' : '')} />
    {box && !centered && <div className="tour-spot" style={box} />}
    <div ref={card} className={'tour-card' + (centered ? ' centered' : pos ? '' : ' sheet')} style={pos && !centered ? pos : undefined} hidden={!ready}>
      <div className="tour-progress" aria-hidden="true">{steps.map((_, k) => <i key={k} className={k <= i ? 'on' : ''} />)}</div>
      <h2 id="tour-title">{step.title}</h2>
      <p>{step.text}</p>
      <div className="tour-buttons">
        {!last && <button className="tour-skip" onClick={finish}>Preskočiť návod</button>}
        <span className="tour-count">{i + 1} / {steps.length}</span>
        {i > 0 && <button className="secondary compact" onClick={() => setI(i - 1)}>Späť</button>}
        <button className="primary compact tour-next" onClick={() => last ? finish() : setI(i + 1)}>{i === 0 ? 'Začať' : last ? 'Rozumiem' : 'Ďalej'}</button>
      </div>
    </div>
  </div>;
}

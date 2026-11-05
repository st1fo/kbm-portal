"""Výpočty dochádzky Denníka, port z pôvodného samostatného pracovny-dennik.html (unwrapSegs, calcRec, activeKey, statusOf,
validateRec, clockIn/closeSeg/undoOut). Čisté funkcie bez DB, aby sa dali testovať rovnakými prípadmi ako JS.

Deň = {'segs': [{'in': 'HH:MM'|None, 'out': 'HH:MM'|None}, ...], 'done': bool}, uložený pod dňom, keď zmena ZAČALA.
Čas menší ako predchádzajúci v poradí in1 → out1 → in2 → out2 … znamená nasledujúci deň (Pi 18:00 → So 02:00).
Medzery medzi úsekmi sú prestávky. Všetky minúty sú od polnoci dňa začiatku (môžu byť ≥ 1440).
"""
from copy import deepcopy
from datetime import date, datetime, timedelta

MAX_SHIFT_MIN = 24 * 60   # prvý príchod → posledný odchod najviac 24 h
OPEN_SHIFT_MIN = 20 * 60  # neuzavretý deň zo včera ostáva „aktívny“ ešte 20 h od prvého príchodu


class PunchConflict(ValueError):
    """Pípnutie nesedí s už uloženými časmi (napr. starší čas po novšom z iného zariadenia)."""


def hm_to_min(hm):
    if not hm: return None
    h, m = hm.split(':')
    return int(h) * 60 + int(m)


def min_to_hm(m):
    """Minúty od polnoci dňa začiatku → ('HH:MM', nasledujúci_deň)."""
    if m is None: return None, False
    m = int(round(m))
    return f'{(m // 60) % 24:02d}:{m % 60:02d}', m >= 1440


def has_rec(rec):
    return bool(rec and rec.get('segs'))


def unwrap(segs):
    prev = None
    out = []
    for sg in segs or []:
        o = {}
        for f in ('in', 'out'):
            m = hm_to_min(sg.get(f))
            if m is None:
                o[f] = None
                continue
            if prev is not None:
                while m < prev: m += 1440
            o[f] = m
            prev = m
        out.append(o)
    return out


def first_in(segs):
    for x in unwrap(segs):
        if x['in'] is not None: return x['in']
    return None


def minutes_since_day(day: date, now: datetime) -> float:
    """Minúty od polnoci `day` podľa miestneho času `now` (nástenné hodiny, rovnako ako HH:MM v úsekoch)."""
    return (now.date() - day).days * 1440 + now.hour * 60 + now.minute + now.second / 60


def active_day(records: dict, now: datetime) -> date:
    """activeKey(): dnešok, ak má záznam; inak včerajšok, ak jeho zmena ešte beží (do 20 h); inak dnešok."""
    t = now.date()
    if has_rec(records.get(t)): return t
    y = t - timedelta(days=1)
    ry = records.get(y)
    if has_rec(ry) and not ry.get('done'):
        fi = first_in(ry['segs'])
        if fi is not None and minutes_since_day(y, now) - fi < OPEN_SHIFT_MIN: return y
    return t


def status_of(rec):
    if not has_rec(rec): return 'out'
    if rec.get('done'): return 'done'
    ls = rec['segs'][-1]
    return 'in' if ls.get('in') and not ls.get('out') else 'break'


def calc(segs, live=False, now_min=None):
    """calcRec(): odpracované/prestávky v minútach; `live` = aktívny neuzavretý deň, `now_min` = minutes_since_day."""
    out = {'work': 0.0, 'brk': 0.0, 'open': False, 'first': None, 'last': None, 'count': 0, 'break_running': False}
    if not segs: return out
    u = unwrap(segs)
    prev_out = None
    for i, sg in enumerate(u):
        if sg['in'] is None: continue
        out['count'] += 1
        if out['first'] is None: out['first'] = sg['in']
        if prev_out is not None and sg['in'] > prev_out: out['brk'] += sg['in'] - prev_out
        if sg['out'] is not None: end = sg['out']
        elif live and i == len(u) - 1: end = max(now_min, sg['in'])
        else: end = None
        if end is not None:
            out['work'] += end - sg['in']
            out['last'] = end
            prev_out = end
        else:
            prev_out = None
    ls = u[-1]
    if live and ls['out'] is not None:
        out['break_running'] = True
        if now_min > ls['out']: out['brk'] += now_min - ls['out']
    out['open'] = live
    return out


def calc_day(day: date, rec, now: datetime, active: date):
    """calc() pre uložený deň vrátane rozhodnutia, či je „živý“."""
    live = day == active and has_rec(rec) and not rec.get('done')
    return calc(rec['segs'] if rec else [], live, minutes_since_day(day, now))


def validate(segs):
    """validateRec(): text chyby alebo ''."""
    segs = segs or []
    for i, s in enumerate(segs):
        if not s.get('in') and s.get('out'): return f'Pozor: úsek {i + 1} nemá čas príchodu.'
        if s.get('in') and not s.get('out') and i < len(segs) - 1 and any(x.get('in') for x in segs[i + 1:]):
            return f'Pozor: úsek {i + 1} nemá čas odchodu (nechať prázdny môže len posledný úsek).'
    first = last = None
    for x in unwrap(segs):
        for m in (x['in'], x['out']):
            if m is None: continue
            if first is None: first = m
            last = m
    if first is not None and last - first > MAX_SHIFT_MIN:
        return 'Pozor: od prvého príchodu po posledný odchod by to bolo viac ako 24 h — skontroluj poradie časov.'
    return ''


def clean(segs):
    """cleanRec(): zahodí úplne prázdne riadky."""
    return [{'in': s.get('in') or None, 'out': s.get('out') or None} for s in segs or [] if s.get('in') or s.get('out')]


def apply_punch(kind: str, at: datetime, records: dict):
    """Pípnutie v čase `at` (miestny čas). `records` obsahuje aspoň dni at.date() a at.date()-1.

    Vráti (deň, nový_záznam) alebo None, ak pípnutie k stavu nesedí (no-op, napr. dvakrát „odchod“).
    """
    ak = active_day(records, at)
    st = status_of(records.get(ak))
    hm = f'{at.hour:02d}:{at.minute:02d}'
    stamped = None  # (index úseku, pole), ktoré sa práve zapísalo
    if kind == 'in':  # clockIn: prvý príchod, návrat z prestávky alebo nový príchod po odchode
        if st == 'in': return None
        k = ak if st in ('break', 'done') else at.date()
        rec = deepcopy(records.get(k)) if has_rec(records.get(k)) else {'segs': [], 'done': False}
        rec['segs'].append({'in': hm, 'out': None})
        rec['done'] = False
        stamped = (len(rec['segs']) - 1, 'in')
    elif kind in ('break', 'out'):  # closeSeg(done)
        if st not in (('in',) if kind == 'break' else ('in', 'break')): return None
        k = ak
        rec = deepcopy(records[k])
        ls = rec['segs'][-1]
        if ls.get('in') and not ls.get('out'):
            ls['out'] = hm
            stamped = (len(rec['segs']) - 1, 'out')
        if kind == 'out': rec['done'] = True
    elif kind == 'undo':  # undoOut: „neodišiel som“, otvorí posledný úsek
        if st != 'done': return None
        k = ak
        rec = deepcopy(records[k])
        rec['segs'][-1]['out'] = None
        rec['done'] = False
    else:
        raise ValueError(kind)
    if stamped:
        i, f = stamped
        expected = (at.date() - k).days * 1440 + at.hour * 60 + at.minute
        if unwrap(rec['segs'])[i][f] != expected:
            raise PunchConflict('Čas pípnutia je skorší ako posledný zapísaný čas dňa. Skontrolujte dochádzku.')
    err = validate(rec['segs'])
    if err: raise PunchConflict(err)
    return k, rec

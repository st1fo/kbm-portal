"""Mesačný výkaz hodín z dochádzky (plánu v kalendári), aj pre ľudí, ktorí nepoužívajú Denník.

Pravidlá (dohodnuté s vedúcim):
- Fond = pracovné dni mesiaca (bez víkendov a sviatkov) × denný úväzok človeka.
- Deň s pracovnou kategóriou (Kancelária, Práca z domu, Služobná cesta, Školenie…) = úväzok mínus schválené
  absencie na časť dňa (žiadosť s počtom hodín, napr. lekár 2 h).
- Celodenná absencia (Dovolenka, PN, …) = úväzok v hodinách danej absencie.
- Pracovný deň bez záznamu sa počíta ako odpracovaný, ale vo výkaze je označený na kontrolu.
- Sviatok v pracovný deň sa vykáže zvlášť. Pípnutia z Denníka sa do výkazu nerátajú.
"""
import io
from collections import defaultdict
from datetime import date
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from sqlalchemy import select
from sqlalchemy.orm import Session
from .db import get_db
from .models import User, AttendanceType, Attendance, LeaveRequest, Holiday
from .security import current_user
from .common import today, days, month_bounds, get_settings, is_work
from .directory import visible_user_ids, restrict_to_group

router = APIRouter(prefix='/api/hours', tags=['hours'])
WEEKDAYS = ['po', 'ut', 'st', 'št', 'pi', 'so', 'ne']


def r2(x: float) -> float:
    return round(x + 1e-9, 2)


def people(db, u, group: str | None, user_id: int | None) -> list[User]:
    if u.role != 'admin':
        ids = {u.id}
    else:
        ids = restrict_to_group(db, visible_user_ids(db, u), group)
    if user_id is not None:
        if ids is not None and user_id not in ids: raise HTTPException(403, 'Tento výkaz nemôžete zobraziť.')
        ids = {user_id}
    q = select(User).order_by(User.name)
    q = q.where(User.id.in_(ids)) if ids is not None else q.where(User.active == True)  # noqa: E712
    return [x for x in db.scalars(q) if x.active or x.id == u.id or user_id is not None]


def report(db, users: list[User], start: date, end: date) -> dict:
    weekends = set(get_settings(db)['weekends'])
    holidays = {h.date: h.name for h in db.scalars(select(Holiday).where(Holiday.date.between(start, end)))}
    types = {t.id: t for t in db.scalars(select(AttendanceType))}
    uids = [x.id for x in users]
    entries = {(e.user_id, e.date): e for e in db.scalars(select(Attendance).where(Attendance.user_id.in_(uids), Attendance.date.between(start, end)))}
    partial = defaultdict(list)
    for r in db.scalars(select(LeaveRequest).where(LeaveRequest.user_id.in_(uids), LeaveRequest.status == 'approved',
                                                   LeaveRequest.hours.is_not(None), LeaveRequest.start_date.between(start, end))):
        partial[(r.user_id, r.start_date)].append(r)
    now = today()
    used_types: set[str] = set()
    out = []
    for p in users:
        daily = p.daily_hours
        fund = worked = holiday_h = 0.0
        absences: dict[str, float] = defaultdict(float)
        missing, rows = [], []
        for d in days(start, end):
            workday = d.weekday() not in weekends and d not in holidays
            e = entries.get((p.id, d))
            t = types.get(e.type_id) if e else None
            parts = partial.get((p.id, d), [])
            row = {'date': d.isoformat(), 'weekday': WEEKDAYS[d.weekday()], 'workday': workday, 'plan': t.name if t else '',
                   'worked': 0.0, 'absence': [], 'flag': '', 'note': e.note if e else ''}
            if workday: fund += daily
            if t and not is_work(t):                       # celodenná absencia
                if workday:
                    absences[t.name] += daily; used_types.add(t.name)
                    row['absence'].append({'type': t.name, 'hours': daily})
            elif t or workday:                                   # pracovný deň (s plánom alebo bez záznamu)
                off = 0.0
                for r in parts:
                    name = types[r.type_id].name if r.type_id in types else 'Absencia'
                    h = min(r.hours, daily - off)
                    if h <= 0: continue
                    off += h; absences[name] += h; used_types.add(name)
                    row['absence'].append({'type': name, 'hours': h, 'note': r.note})
                row['worked'] = r2(daily - off)
                worked += daily - off
                if not t:
                    row['flag'] = 'missing' if d <= now else 'future'
                    if d <= now: missing.append(d.isoformat())
                elif not workday:
                    row['flag'] = 'holiday_work' if d in holidays else 'weekend'
            elif d in holidays and d.weekday() not in weekends:
                holiday_h += daily; row['flag'] = 'holiday'; row['plan'] = holidays[d]
            rows.append(row)
        absence_total = sum(absences.values())
        out.append({'user_id': p.id, 'name': p.name, 'daily_hours': daily, 'fund': r2(fund), 'worked': r2(worked),
                    'holiday': r2(holiday_h), 'absences': {k: r2(v) for k, v in absences.items()}, 'absence_total': r2(absence_total),
                    # rozdiel: čo je navyše (víkendy) alebo chýba voči fondu
                    'difference': r2(worked + absence_total - fund), 'missing': missing, 'days': rows})
    order = [t.name for t in sorted(types.values(), key=lambda t: t.id) if t.name in used_types]
    return {'start': start.isoformat(), 'end': end.isoformat(), 'month_open': end >= now, 'types': order, 'people': out}


@router.get('')
def hours(month: str, group: str | None = None, user_id: int | None = None, u=Depends(current_user), db: Session = Depends(get_db)):
    start, end = month_bounds(month)
    return report(db, people(db, u, group, user_id), start, end)


def num(x: float):
    return int(x) if float(x).is_integer() else x


@router.get('/export')
def export(month: str, group: str | None = None, user_id: int | None = None, u=Depends(current_user), db: Session = Depends(get_db)):
    start, end = month_bounds(month)
    data = report(db, people(db, u, group, user_id), start, end)
    wb = Workbook()
    ws = wb.active; ws.title = 'Súhrn'
    head = ['Zamestnanec', 'Úväzok (h/deň)', 'Fond (h)', 'Odpracované (h)', *[f'{t} (h)' for t in data['types']],
            'Absencie spolu (h)', 'Sviatky (h)', 'Rozdiel (h)', 'Dni bez záznamu']
    ws.append(head)
    for p in data['people']:
        ws.append([p['name'], num(p['daily_hours']), num(p['fund']), num(p['worked']), *[num(p['absences'].get(t, 0)) for t in data['types']],
                   num(p['absence_total']), num(p['holiday']), num(p['difference']), len(p['missing'])])
    detail = wb.create_sheet('Po dňoch')
    detail.append(['Zamestnanec', 'Dátum', 'Deň', 'Plán', 'Odpracované (h)', 'Absencia', 'Absencia (h)', 'Upozornenie', 'Poznámka'])
    flags = {'missing': 'Bez záznamu – započítané ako odpracované', 'future': 'Bez plánu (budúci deň)', 'weekend': 'Práca cez víkend',
             'holiday_work': 'Práca vo sviatok', 'holiday': 'Sviatok'}
    warn = PatternFill('solid', fgColor='FFF4D6')
    for p in data['people']:
        for r in p['days']:
            if not r['workday'] and not r['plan'] and not r['worked']: continue
            detail.append([p['name'], date.fromisoformat(r['date']).strftime('%d.%m.%Y'), r['weekday'], r['plan'], num(r['worked']),
                           ', '.join(a['type'] for a in r['absence']), num(sum(a['hours'] for a in r['absence'])), flags.get(r['flag'], ''),
                           '; '.join(x for x in [r['note'], *[a.get('note', '') for a in r['absence']]] if x)])
            if r['flag'] == 'missing':
                for c in detail[detail.max_row]: c.fill = warn
    for sheet in (ws, detail):
        for c in sheet[1]: c.font = Font(bold=True)
        sheet.freeze_panes = 'B2'
        for row in sheet.iter_rows():
            for c in row:
                if isinstance(c.value, str): c.data_type = 's'   # žiadne vzorce z poznámok
        for col in sheet.columns:
            sheet.column_dimensions[col[0].column_letter].width = min(48, max(10, *(len(str(c.value or '')) + 2 for c in col)))
    buf = io.BytesIO(); wb.save(buf); buf.seek(0)
    name = f'KBM-vykaz-hodin-{month}.xlsx'
    return StreamingResponse(buf, media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                             headers={'Content-Disposition': f'attachment; filename="{name}"'})

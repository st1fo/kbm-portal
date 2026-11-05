"""Prípady z pôvodného pracovny-dennik.html (calcRec, unwrapSegs, activeKey, validateRec, pípanie)."""
from datetime import date, datetime
import pytest
from app.common import TZ
from app import journal_calc as jc

D = date(2026, 11, 5)
def at(day, hm, s=0):
    h, m = map(int, hm.split(':'))
    return datetime(day.year, day.month, day.day, h, m, s, tzinfo=TZ)
def segs(*pairs): return [{'in': a, 'out': b} for a, b in pairs]


def test_single_segment_and_breaks():
    c = jc.calc(segs(('08:00', '12:00'), ('12:30', '16:45')))
    assert (c['work'], c['brk'], c['count'], c['first'], c['last'], c['open']) == (495, 30, 2, 480, 1005, False)


def test_overnight_end_and_segment_starting_after_midnight():
    # Pi 18:00 → So 02:00; druhý úsek začína po polnoci
    u = jc.unwrap(segs(('18:00', '23:30'), ('00:15', '02:00')))
    assert u == [{'in': 1080, 'out': 1410}, {'in': 1455, 'out': 1560}]
    c = jc.calc(segs(('18:00', '23:30'), ('00:15', '02:00')))
    assert (c['work'], c['brk']) == (330 + 105, 45)
    assert jc.min_to_hm(1560) == ('02:00', True)


def test_running_segment_and_running_break():
    c = jc.calc(segs(('08:00', None)), live=True, now_min=600)
    assert (c['work'], c['open'], c['break_running']) == (120, True, False)
    c = jc.calc(segs(('08:00', '12:00')), live=True, now_min=750)
    assert (c['work'], c['brk'], c['break_running']) == (240, 30, True)
    # now pred začiatkom (napr. posun hodín) → nikdy záporný čas
    assert jc.calc(segs(('08:00', None)), live=True, now_min=400)['work'] == 0


def test_open_segment_on_inactive_day_counts_only_closed_parts():
    c = jc.calc(segs(('08:00', '12:00'), ('13:00', None)), live=False, now_min=5000)
    assert (c['work'], c['brk'], c['count'], c['last']) == (240, 60, 2, 720)


def test_active_day_overnight_window():
    y = date(2026, 11, 4)
    recs = {y: {'segs': segs(('22:00', None)), 'done': False}}
    assert jc.active_day(recs, at(D, '06:00')) == y          # 8 h od príchodu
    assert jc.active_day(recs, at(D, '17:59')) == y          # 19 h 59 min
    assert jc.active_day(recs, at(D, '18:00')) == D          # 20 h → nový deň
    assert jc.active_day({y: {'segs': segs(('22:00', '23:00')), 'done': True}}, at(D, '06:00')) == D
    recs[D] = {'segs': segs(('09:00', None)), 'done': False}
    assert jc.active_day(recs, at(D, '10:00')) == D          # dnešok má záznam


def test_status():
    assert jc.status_of(None) == 'out'
    assert jc.status_of({'segs': segs(('08:00', None)), 'done': False}) == 'in'
    assert jc.status_of({'segs': segs(('08:00', '12:00')), 'done': False}) == 'break'
    assert jc.status_of({'segs': segs(('08:00', '12:00')), 'done': True}) == 'done'


@pytest.mark.parametrize('rows,msg', [
    ((('08:00', '12:00'),), ''),
    (((None, '12:00'),), 'úsek 1 nemá čas príchodu'),
    ((('08:00', None), ('13:00', '14:00')), 'úsek 1 nemá čas odchodu'),
    ((('08:00', '07:00'), ('09:00', '10:00')), 'viac ako 24 h'),
    ((('08:00', None),), ''),
])
def test_validate(rows, msg):
    err = jc.validate(segs(*rows))
    assert (msg in err) if msg else err == ''


def test_punch_flow_in_break_in_out_undo():
    recs = {}
    k, r = jc.apply_punch('in', at(D, '08:00'), recs); recs[k] = r
    assert jc.apply_punch('in', at(D, '08:05'), recs) is None           # už v práci
    k, r = jc.apply_punch('break', at(D, '12:00'), recs); recs[k] = r
    assert jc.status_of(r) == 'break'
    k, r = jc.apply_punch('in', at(D, '12:30'), recs); recs[k] = r
    k, r = jc.apply_punch('out', at(D, '16:30'), recs); recs[k] = r
    assert r == {'segs': segs(('08:00', '12:00'), ('12:30', '16:30')), 'done': True}
    assert jc.apply_punch('out', at(D, '16:31'), recs) is None          # už odišiel
    k, r = jc.apply_punch('undo', at(D, '16:40'), recs); recs[k] = r
    assert r['segs'][-1]['out'] is None and not r['done']
    k, r = jc.apply_punch('out', at(D, '17:00'), recs)
    assert r['segs'][-1] == {'in': '12:30', 'out': '17:00'} and r['done']


def test_punch_out_from_break_and_again_in_after_done():
    recs = {D: {'segs': segs(('08:00', '12:00')), 'done': False}}
    k, r = jc.apply_punch('out', at(D, '12:10'), recs)
    assert r == {'segs': segs(('08:00', '12:00')), 'done': True}     # odchod počas prestávky nemení časy
    recs[k] = r
    k, r = jc.apply_punch('in', at(D, '14:00'), recs)                 # „Pipnúť znova príchod“ v ten istý deň
    assert k == D and r['segs'][-1] == {'in': '14:00', 'out': None} and not r['done']


def test_overnight_punch_lands_on_start_day():
    y = date(2026, 11, 4)
    recs = {}
    k, r = jc.apply_punch('in', at(y, '22:00'), recs); recs[k] = r
    k, r = jc.apply_punch('out', at(D, '02:30'), recs)
    assert k == y and r['segs'] == segs(('22:00', '02:30')) and r['done']
    assert jc.calc(r['segs'])['work'] == 270


def test_punch_older_than_last_stamp_is_conflict():
    recs = {D: {'segs': segs(('08:00', '12:00')), 'done': False}}
    with pytest.raises(jc.PunchConflict):
        jc.apply_punch('in', at(D, '11:00'), recs)


def test_clean_drops_empty_rows():
    assert jc.clean([{'in': '', 'out': ''}, {'in': '08:00', 'out': ''}]) == [{'in': '08:00', 'out': None}]

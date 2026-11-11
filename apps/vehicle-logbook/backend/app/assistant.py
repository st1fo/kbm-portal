"""Chat-driven monthly wizard.

The draft (chat, receipts, proposed trips) lives in `assistant_sessions` until the user presses
"Schváliť a uložiť výkaz". Gemini only proposes values; this module validates every change as an
allowed operation on one draft row, recomputes kilometres and odometers deterministically, and writes
trips and fuel purchases to the logbook in a single transaction on approval.
"""
import calendar
import hashlib
import json
import re
from collections import namedtuple
from copy import deepcopy
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import delete, func, select
from sqlalchemy.orm.attributes import flag_modified

from . import distances, gemini, planner
from .auth import current_user, db_session
from .db import Access, AssistantFile, AssistantSession, Audit, Destination, Fuel, Holiday, MonthlyDraft, Trip, Vehicle, now
from .ledger import ledger, month_of, next_month, rounded, tank_tolerance

router = APIRouter(prefix="/api")
Db = Depends(db_session)
Member = Depends(current_user)

MAX_FILE_BYTES = 8_000_000
MAX_FILES = 20
MAX_MESSAGE = 2000
MAX_MESSAGES = 300
MAX_TRIPS = 120
MAX_GEMINI_CALLS = 150
# The per-draft limit resets with a new draft; this one is per user and day (in memory, enough for one API process).
MAX_GEMINI_PER_DAY = 300
_spent = {}


def spend(user_id):
    """Count one Gemini call of the user; refuse when the daily budget is used up."""
    now = datetime.now().timestamp()
    calls = _spent[user_id] = [t for t in _spent.get(user_id, []) if t > now - 86400]
    if len(calls) >= MAX_GEMINI_PER_DAY:
        raise gemini.AssistantError("Dnešný limit požiadaviek na asistenta je vyčerpaný. Údaje doplňte ručne alebo pokračujte zajtra.")
    calls.append(now)
HISTORY = 10
ALLOWED = {"image/jpeg": "JPG", "image/png": "PNG", "image/webp": "WebP", "image/heic": "HEIC",
           "image/heif": "HEIF", "application/pdf": "PDF"}
D = Decimal


class Problem(ValueError):
    """A validation result phrased as a question or instruction for the user."""


def fail(message, code=422):
    raise HTTPException(code, message)


def today():
    return datetime.now(ZoneInfo("Europe/Bratislava")).date()


def json_value(data):
    return jsonable_encoder(data, custom_encoder={Decimal: str})


def sk_day(value):
    day = value if isinstance(value, date) else date.fromisoformat(value)
    return f"{day.day}. {day.month}."


def km(value):
    return f"{value:,}".replace(",", " ") + " km"


NO_DESTINATION = "Doplňte cieľ"   # placeholder of a trip added with „+“ in the month view; cannot be approved


def plural(count, one, few, many):
    return one if count == 1 else few if 2 <= count <= 4 else many


# --- Input models ------------------------------------------------------------------------------

Text = Annotated[str, StringConstraints(strip_whitespace=True, max_length=150)]


class SessionIn(BaseModel):
    month: date


class MessageIn(BaseModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_MESSAGE)]


class Operation(BaseModel):
    """The only changes allowed on a draft, used by both the chat and the editable tables."""
    action: Literal["set_odometers", "set_replace", "update_trip", "add_trip", "delete_trip", "add_receipt", "update_receipt",
                    "delete_receipt", "dismiss_duplicate", "delete_file"]
    row: Annotated[str, StringConstraints(max_length=40)] | None = None
    day: date | None = None
    destination: Text | None = None
    origin: Text | None = None
    category: Literal["mesto", "mimo mesto"] | None = None
    purpose: Text | None = None
    km: Annotated[int, Field(ge=1, le=5000)] | None = None
    liters: Annotated[Decimal, Field(gt=0, le=1000, decimal_places=3)] | None = None
    gross: Annotated[Decimal, Field(gt=0, le=10000, decimal_places=2)] | None = None
    currency: Annotated[str, StringConstraints(strip_whitespace=True, to_upper=True, max_length=3)] | None = None
    vat: Annotated[Decimal, Field(ge=0, le=100, decimal_places=2)] | None = None
    odo: Annotated[int, Field(ge=0, le=10_000_000)] | None = None
    station: Text | None = None
    start_odo: Annotated[int, Field(ge=0, le=10_000_000)] | None = None
    end_odo: Annotated[int, Field(ge=0, le=10_000_000)] | None = None
    end_day: date | None = None
    replace: bool | None = None
    clear: list[Literal["odo", "start_odo", "end_odo", "end_day", "day", "liters", "gross", "km"]] = []


class OperationsIn(BaseModel):
    operations: Annotated[list[Operation], Field(min_length=1, max_length=50)]


class PlanIn(BaseModel):
    rebuild: bool = True


class ApproveIn(BaseModel):
    version: int
    receipts_checked: bool = False


# --- Draft helpers -----------------------------------------------------------------------------

def blank_state():
    return dict(start_odo=None, end_odo=None, end_day=None, origin=distances.HOME, routes=[], files=[], receipts=[], moves=[],
                replace_existing=False, trips=[], messages=[], highlight=[], problems=[],
                counter={"B": 0, "J": 0}, gemini_calls=0, fingerprint=None, stale=False)


def vehicle_for(db, user, identifier, lock=False):
    query = select(Vehicle).where(Vehicle.id == identifier)
    if lock:
        query = query.with_for_update()
    vehicle = db.scalar(query)
    if not vehicle or (not user.admin and not db.get(Access, (user.id, identifier))):
        fail("Vozidlo nie je dostupné.", 404)
    return vehicle


def session_for(db, user, identifier, lock=True):
    """Load a draft the user may edit; the vehicle row is locked first so writes serialise per vehicle."""
    session = db.get(AssistantSession, identifier)
    if not session or (session.user_id != user.id and not user.admin):
        fail("Koncept sa nenašiel.", 404)
    vehicle = vehicle_for(db, user, session.vehicle_id, lock)
    if lock:
        session = db.scalar(select(AssistantSession).where(AssistantSession.id == identifier).with_for_update())
        db.refresh(session)
    state = deepcopy(session.state)
    for key, value in blank_state().items():
        state.setdefault(key, value)
    return session, vehicle, state


def store(session, state, changed=None):
    if changed is not None:
        state["highlight"] = sorted(set(changed))
    session.state = deepcopy(state)
    flag_modified(session, "state")
    session.version += 1
    session.updated_at = now()


def require_draft(session):
    if session.status != "draft":
        fail("Výkaz z tohto konceptu je už uložený. Ďalšie úpravy robte v knihe jázd.", 409)


def next_id(state, prefix):
    state["counter"][prefix] += 1
    return f"{prefix}{state['counter'][prefix]}"


def find(state, kind, row):
    for item in state[kind]:
        if item["id"] == (row or "").upper():
            return item
    raise Problem(f"Riadok {row or '?'} v koncepte neexistuje.")


def month_bounds(session):
    first = session.month
    return first, next_month(first) - timedelta(days=1)


def cached(db, key, compute):
    """Per-request memo (the SQLAlchemy session lives for one request): the month's logbook rows, holidays and the
    vehicle's history do not change while a draft is being recalculated, but are needed by many helpers."""
    memo = db.info.setdefault("assistant", {})
    if key not in memo:
        memo[key] = compute()
    return memo[key]


def expected_start(db, vehicle, month):
    previous = db.scalar(select(Trip).where(Trip.vehicle_id == vehicle.id, Trip.day < month)
                         .order_by(Trip.day.desc(), Trip.end_odo.desc()).limit(1))
    return previous.end_odo if previous else vehicle.initial_odo


def catalogue(db, vehicle):
    return cached(db, ("catalogue", vehicle.id), lambda: list(db.scalars(
        select(Destination).where(Destination.vehicle_id == vehicle.id).order_by(Destination.name))))


def holidays(db, vehicle):
    return cached(db, ("holidays", vehicle.id), lambda: set(db.scalars(select(Holiday.day).where(Holiday.vehicle_id == vehicle.id))))


def workdays(db, vehicle, start, stop, fallback=True):
    """Weekdays without a vehicle holiday in start..stop; with `fallback`, every day when there is no such day."""
    days = [start + timedelta(days=i) for i in range((stop - start).days + 1)]
    free = holidays(db, vehicle)
    work = [day for day in days if day.weekday() < 5 and day not in free]
    return work or (days if fallback else [])


def norm(vehicle, category):
    """Norm consumption in l/100 km."""
    return D(vehicle.city_rate if category == "mesto" else vehicle.outside_rate)


def history_km(db, vehicle, destination, origin=None):
    """Average length of this vehicle's recorded trips to the destination from the same origin (trips without an
    origin count too); manual trips are preferred over earlier estimates (monthly drafts, Excel estimates)."""
    name = planner.normalized(destination or "")
    if not name:
        return None
    start = planner.normalized(origin or distances.HOME)
    rows = cached(db, ("history", vehicle.id), lambda: [
        (planner.normalized(dest), planner.normalized(place or ""), length, draft) for dest, place, length, draft in db.execute(
            select(Trip.destination, Trip.origin, Trip.end_odo - Trip.start_odo, Trip.draft_id.is_not(None) | Trip.estimated)
            .where(Trip.vehicle_id == vehicle.id, Trip.destination != "").order_by(Trip.day.desc()).limit(500))])
    same = [(length, draft) for dest, place, length, draft in rows if dest == name and length > 0 and place in ("", start)]
    lengths = [length for length, draft in same if not draft] or [length for length, _ in same]
    return round(sum(lengths) / len(lengths)) if lengths else None


def known_km(db, vehicle, state, destination, origin=None):
    """Round-trip km: what this vehicle really drove there (logbook history), then its catalogue for this origin
    (implausibly short entries are skipped), then the Žilina table, then the draft's routes."""
    origin = origin or state.get("origin") or distances.HOME
    recorded = history_km(db, vehicle, destination, origin)
    if recorded and (recorded >= 20 or distances.is_city(destination)):   # same sanity rule as for the catalogue below
        return recorded
    name = planner.normalized(destination or "")
    matches = [item for item in catalogue(db, vehicle) if planner.normalized(item.name) == name
               and (not item.origin or planner.normalized(item.origin) == planner.normalized(origin))
               and (item.distance * 2 >= 20 or distances.is_city(destination))]
    if matches:
        return matches[0].distance * 2
    builtin = distances.round_trip(destination, origin)
    if builtin:
        return builtin
    for route in state.get("routes", []):
        if planner.normalized(route["destination"]) == name and route.get("approximate_km"):
            return route["approximate_km"]
    return None


def month_rows(db, vehicle, month):
    """Trips and fuel purchases of the month already in the logbook."""
    def load():
        stop = next_month(month)
        trips = list(db.scalars(select(Trip).where(Trip.vehicle_id == vehicle.id, Trip.day >= month, Trip.day < stop)
                                .order_by(Trip.day, Trip.start_odo, Trip.id)))
        fuels = list(db.scalars(select(Fuel).where(Fuel.vehicle_id == vehicle.id, Fuel.day >= month, Fuel.day < stop)
                                .order_by(Fuel.day, Fuel.clock, Fuel.id)))
        return trips, fuels
    return cached(db, ("month", vehicle.id, month), load)


def month_trips(db, vehicle, month):
    """Trips already saved in the month: estimates from an earlier monthly draft can be replaced, manual ones stay
    and the draft only fills the kilometres between them."""
    rows = month_rows(db, vehicle, month)[0]
    replaceable = [row for row in rows if row.draft_id]
    manual = [row for row in rows if not row.draft_id]
    return dict(count=len(rows), replaceable=len(replaceable), manual=len(manual),
                km=sum(row.end_odo - row.start_odo for row in replaceable),
                manual_km=sum(row.end_odo - row.start_odo for row in manual), ids=[row.id for row in replaceable])


def saved_rows(db, vehicle, session, state):
    """Fuel purchases and trips of the month already in the logbook (e.g. a FleetCor invoice, trips entered by hand).
    They are read-only in the draft; trips replaced by this draft (earlier estimates) are left out."""
    trips, fuels = month_rows(db, vehicle, session.month)
    if state.get("replace_existing"):
        trips = [trip for trip in trips if not trip.draft_id]
    return trips, fuels


def existing_text(info, month):
    return (f"Mesiac {month.month}/{month.year} už obsahuje jazdy uložené skôr ako mesačný odhad: {info['replaceable']} "
            f"({km(info['km'])}). Ak ich chcete nahradiť týmto návrhom, zapnite vľavo voľbu „Nahradiť uložený odhad“. Staré "
            "jazdy sa odstránia až pri schválení výkazu, v tej istej transakcii.")


def saved_text(db, vehicle, session, state):
    """Greeting line about rows loaded from the logbook."""
    trips, fuels = saved_rows(db, vehicle, session, state)
    manual = [trip for trip in trips if not trip.draft_id]
    parts = []
    if fuels:
        parts.append(f"{len(fuels)} {plural(len(fuels), 'tankovanie', 'tankovania', 'tankovaní')} "
                     f"({liters(sum((f.liters for f in fuels), D(0)))})")
    if manual:
        parts.append(f"{len(manual)} {plural(len(manual), 'jazdu', 'jazdy', 'jázd')} "
                     f"({km(sum(t.end_odo - t.start_odo for t in manual))})")
    if not parts:
        return ""
    text = f"Z knihy jázd som načítal {' a '.join(parts)} z tohto mesiaca. Vidíte ich v prehľade so štítkom „v knihe“ a započítam ich."
    if fuels:
        text += " Bločky k týmto tankovaniam už nenahrávajte, započítali by sa dvakrát."
    return text


def decimal_text(value, places):
    return str(rounded(D(str(value)), places)) if value is not None else None


def remove_file(db, state, file_id):
    state["files"] = [item for item in state["files"] if item["id"] != file_id]
    if not any(item.get("file_id") == file_id for item in state["receipts"]):
        db.execute(delete(AssistantFile).where(AssistantFile.id == file_id))


# --- Receipts ---------------------------------------------------------------------------------

def default_vat(day):
    return "23" if (day or date.today().isoformat()) >= "2025-01-01" else "20"


def receipt_checks(db, vehicle, session, state):
    """Mark missing fields and possible duplicates; returns problems that block approval."""
    first, last = month_bounds(session)
    existing = month_rows(db, vehicle, session.month)[1]
    problems = []
    receipts = sorted(state["receipts"], key=lambda r: (r.get("day") or "9999", r["id"]))
    for index, receipt in enumerate(receipts):
        missing = [field for field in ("day", "liters", "gross") if not receipt.get(field)]
        if receipt.get("currency") and receipt["currency"] != "EUR":
            missing.append("currency")
        receipt["missing"] = missing
        if missing:
            names = {"day": "dátum", "liters": "litre", "gross": "suma", "currency": "suma v EUR"}
            problems.append(f"Bloček {receipt['id']}: doplňte {', '.join(names[m] for m in missing)}.")
        if receipt.get("day") and not first.isoformat() <= receipt["day"] <= last.isoformat():
            problems.append(f"Bloček {receipt['id']} má dátum {sk_day(receipt['day'])} mimo vybraného mesiaca.")
        elif receipt.get("day") and receipt["day"] < vehicle.start_date.isoformat():
            problems.append(f"Bloček {receipt['id']} je pred začiatkom evidencie vozidla ({sk_day(vehicle.start_date)}).")
        if receipt.get("liters") and D(receipt["liters"]) > vehicle.capacity:
            problems.append(f"Bloček {receipt['id']}: {receipt['liters']} l presahuje kapacitu nádrže {rounded(vehicle.capacity)} l.")
        duplicate = None
        if receipt.get("day") and receipt.get("liters"):
            for other in receipts[:index]:
                if other.get("day") == receipt["day"] and other.get("liters") == receipt["liters"] and \
                        other.get("gross") == receipt.get("gross"):
                    duplicate = f"rovnaký ako bloček {other['id']}"
            for fuel in existing:
                if fuel.draft_id and fuel.day.isoformat() == receipt["day"] and abs(fuel.liters - D(receipt["liters"])) < D("0.01") \
                        and receipt.get("gross") and abs(fuel.gross - D(receipt["gross"])) < D("0.01"):
                    receipt["duplicate"] = f"tankovanie {sk_day(fuel.day)} je už uložené z mesačného sprievodcu"
                    receipt["duplicate_saved"] = True
                    problems.append(f"Bloček {receipt['id']}: {receipt['duplicate']} ({rounded(fuel.liters)} l, "
                                    f"{rounded(fuel.gross)} €). Odstráňte ho, inak by sa tankovanie započítalo dvakrát.")
                    break
                same_amount = abs(fuel.liters - D(receipt["liters"])) < D("0.01") or \
                    (receipt.get("gross") and abs(fuel.gross - D(receipt["gross"])) < D("0.01"))
                if fuel.day.isoformat() == receipt["day"] and same_amount:
                    duplicate = f"podobné tankovanie {sk_day(fuel.day)} je už v evidencii ({rounded(fuel.liters)} l, {rounded(fuel.gross)} €)"
        if receipt.pop("duplicate_saved", False):
            continue
        receipt["duplicate"] = duplicate
        if duplicate and not receipt.get("duplicate_ok"):
            problems.append(f"Bloček {receipt['id']} je možná duplicita: {duplicate}. Odstráňte ho alebo potvrďte, že nejde o duplicitu.")
    for item in state["files"]:
        if item["status"] == "pending":
            problems.append(f"Súbor {item['name']} ešte nebol spracovaný.")
        elif item["status"] == "error":
            problems.append(f"Súbor {item['name']}: {item['error']} Spracujte ho znova, odstráňte ho alebo pridajte bloček ručne.")
    state["receipts"] = receipts
    return problems


def extract_pending(db, session, state, retry=False):
    """Send unprocessed uploads to Gemini. Each processed file is committed so failures keep the draft.
    Files that already failed are sent again only on an explicit `retry` (the „Skúsiť znova“ button), not with
    every chat message: each attempt uploads the whole file to Gemini again."""
    notes, changed = [], []
    for item in state["files"]:
        if item["status"] != "pending" and not (retry and item["status"] == "error"):
            continue
        stored = db.get(AssistantFile, item["id"])
        if not stored:
            item.update(status="error", error="Súbor sa nenašiel.")
            continue
        if state["gemini_calls"] >= MAX_GEMINI_CALLS:
            raise gemini.AssistantError("Koncept dosiahol limit volaní Gemini. Zvyšné údaje doplňte ručne.")
        state["gemini_calls"] += 1
        spend(session.user_id)
        try:
            result = gemini.extract_receipt(stored.data, stored.mime)
        except gemini.AssistantError as error:
            item.update(status="error", error=str(error))
            store(session, state)
            db.commit()
            raise
        state["receipts"] = [r for r in state["receipts"] if r.get("file_id") != item["id"]]
        if not result.is_receipt or not result.receipts:
            item.update(status="error", error="V súbore sa nenašiel doklad o tankovaní.")
            notes.append(f"V súbore {item['name']} som nenašiel doklad o tankovaní.")
            continue
        for fields in result.receipts[:10]:
            receipt = dict(id=next_id(state, "B"), file_id=item["id"], file_name=item["name"], source="gemini",
                           duplicate_ok=False, unreadable=[])
            receipt.update(clean_receipt(fields))
            # Only fields that are both reported illegible and actually empty need the user's attention.
            receipt["unreadable"] = sorted({name for name in fields.unreadable
                                            if name in {"day", "station", "liters", "gross"} and not receipt.get(name)})
            state["receipts"].append(receipt)
            changed.append(receipt["id"])
        item.update(status="done", error=None)
    return notes, changed


def clean_receipt(fields):
    """Turn Gemini's loosely typed values into draft values; anything implausible becomes empty."""
    values = {}
    try:
        values["day"] = date.fromisoformat(fields.day).isoformat() if fields.day else None
    except ValueError:
        values["day"] = None
    values["station"] = (fields.station or "").strip()[:150]

    def amount(value, limit, places):
        try:
            return decimal_text(value, places) if value is not None and 0 < value <= limit else None
        except InvalidOperation:
            return None
    values["liters"] = amount(fields.liters, 1000, "0.001")
    values["gross"] = amount(fields.gross, 10000, "0.01")
    values["vat"] = decimal_text(fields.vat, "0.01") if fields.vat is not None and 0 <= fields.vat <= 100 else default_vat(values["day"])
    values["currency"] = (fields.currency or "EUR").strip().upper()[:3] or "EUR"
    values["odo"] = fields.odo if fields.odo is not None and 0 <= fields.odo <= 10_000_000 else None
    return values


# --- Schedule ---------------------------------------------------------------------------------
#
# Odometer readings that are known (receipts with an odometer, trips kept in the logbook, the closing reading) cut the
# month into segments whose kilometres are fixed; `distribute` splits each segment's kilometres between its trips by
# their usual length. Refuels without an odometer (FleetCor invoices, most receipts) do not cut anything: around them
# `schedule_for_fuel` only chooses the days of estimated trips so the tank stays between empty and full.

Frame = namedtuple("Frame", "points spans close")  # anchors (day, odo), kept trips (day, start, end), closing (day, odo)


def anchors(db, vehicle, session, state):
    """Odometer readings the schedule must hit: fuel purchases and draft receipts with an odometer, kept trips."""
    fuels = month_rows(db, vehicle, session.month)[1]
    points = {(fuel.day, fuel.odo) for fuel in fuels if fuel.odo is not None}
    points |= {(date.fromisoformat(r["day"]), r["odo"]) for r in state["receipts"] if r.get("odo") is not None and r.get("day")}
    for day, start, end in kept_spans(db, vehicle, session, state):
        points |= {(day, start), (day, end)}
    return sorted(points)


def kept_spans(db, vehicle, session, state):
    """(day, start odo, end odo) of trips that stay in the logbook; the draft plans only the kilometres between them."""
    return [(trip.day, trip.start_odo, trip.end_odo) for trip in saved_rows(db, vehicle, session, state)[0]]


def closing(db, vehicle, session, state):
    """The month's closing anchor (day, odometer, estimated).

    The closing reading can carry the day it was read. Read inside the month → trips are planned only up to that
    day and it is the month's closing state. Read after the month → the closing state on the last day is estimated:
    kilometres since the last known reading are split by working days before and after the month end."""
    first, last = month_bounds(session)
    if state["end_odo"] is None:
        return last, None, False
    read = date.fromisoformat(state["end_day"]) if state.get("end_day") else last
    if read <= last:
        return read, state["end_odo"], False
    points = anchors(db, vehicle, session, state)
    prior_day, prior_odo = points[-1] if points else (max(first, vehicle.start_date) - timedelta(days=1), state["start_odo"])
    if prior_odo is None or state["end_odo"] <= prior_odo:
        return last, state["end_odo"], False
    inside = len(workdays(db, vehicle, prior_day + timedelta(days=1), last, fallback=False))
    total = len(workdays(db, vehicle, prior_day + timedelta(days=1), read, fallback=False))
    if not total:  # no working day at all: split by calendar days
        inside, total = (last - prior_day).days, (read - prior_day).days
    return last, prior_odo + round((state["end_odo"] - prior_odo) * inside / total), True


def closing_text(db, vehicle, session, state):
    day, odo, estimated = closing(db, vehicle, session, state)
    if odo is None or not state.get("end_day"):
        return ""
    read = date.fromisoformat(state["end_day"])
    if estimated:
        return (f"Tachometer ste odčítali {sk_day(read)} ({km(state['end_odo'])}). Stav k {sk_day(day)} som podľa "
                f"pracovných dní odhadol na {km(odo)}; zvyšok patrí do ďalšieho mesiaca.")
    if read < month_bounds(session)[1]:
        return f"Tachometer ste odčítali {sk_day(read)}, jazdy preto navrhujem iba do tohto dňa."
    return ""


def odometer_question(state):
    missing = [label for key, label in (("start_odo", "na začiatku"), ("end_odo", "na konci")) if state[key] is None]
    return f"Aký bol stav tachometra {' a '.join(missing)} mesiaca?" if missing else ""


def check_month(db, vehicle, session, state):
    """Backend rules for the month (closing, existing trips, odometer continuity); returns the planning Frame."""
    if odometer_question(state):
        raise Problem(odometer_question(state))
    if state["end_odo"] <= state["start_odo"]:
        raise Problem("Koncový stav tachometra musí byť vyšší ako počiatočný. Skontrolujte, prosím, obe hodnoty.")
    close_day, close_odo, _ = closing(db, vehicle, session, state)
    spans = kept_spans(db, vehicle, session, state)
    points = anchors(db, vehicle, session, state)
    try:
        planner.month_context(db, vehicle, session.month, state["start_odo"], close_odo, today(), points,
                              replaceable=state.get("replace_existing", False), keep_manual=True)
    except planner.PlanError as error:
        message = str(error)
        if "Mesiac už obsahuje jazdy" in message:
            message = existing_text(month_trips(db, vehicle, session.month), session.month)
        if "Tachometre tankovaní" in message:
            details = ", ".join(f"{sk_day(day)} {km(odo)}" for day, odo in points)
            source = "na bločkoch a v uložených jazdách" if spans else "na bločkoch"
            message = (f"Stavy tachometra {source} ({details}) nesedia s rozsahom {km(state['start_odo'])} – "
                       f"{km(state['end_odo'])} alebo nejdú chronologicky. Ktorá hodnota je správna?")
        raise Problem(message) from error
    return Frame(points, spans, (close_day, close_odo))


def segments_for(session, state, vehicle, frame):
    """Odometer segments between anchors. A segment covered by a trip kept in the logbook is `fixed`: draft trips
    never go there and its kilometres are already accounted for."""
    result, prior_day, prior_odo = [], max(session.month, vehicle.start_date), state["start_odo"]
    for day, odo in [*frame.points, frame.close]:
        fixed = odo > prior_odo and prior_day == day and any(
            span_day == day and start <= prior_odo and odo <= end for span_day, start, end in frame.spans)
        result.append(dict(first=prior_day, last=day, start=prior_odo, end=odo, budget=odo - prior_odo, trips=[], fixed=fixed))
        prior_day, prior_odo = day, odo
    return result


def assign(segments, trips):
    """Place trips (sorted by day) into odometer segments, preferring empty segments on shared days."""
    current = 0
    for trip in trips:
        day = date.fromisoformat(trip["day"])
        options = [i for i, s in enumerate(segments) if i >= current and s["first"] <= day <= s["last"] and not s["fixed"]]
        if not options and day > segments[-1]["last"]:
            raise Problem(f"Jazda {trip['id']} ({sk_day(day)}) je po dni odčítania tachometra ({sk_day(segments[-1]['last'])}). "
                          "Opravte jej dátum alebo dátum odčítania.")
        if not options:
            raise Problem(f"Jazda {trip['id']} ({sk_day(day)}) nenadväzuje chronologicky na tankovania. Skontrolujte jej dátum.")
        index = next((i for i in options if segments[i]["budget"] > 0 and not segments[i]["trips"]),
                     next((i for i in options if segments[i]["budget"] > 0), options[0]))
        segments[index]["trips"].append(trip)
        current = index


def distribute(segments):
    """Exact integer kilometres per segment, keeping confirmed distances and weighting the rest."""
    problems = []
    for segment in segments:
        if segment["fixed"]:
            continue
        trips, budget = segment["trips"], segment["budget"]
        span = f"medzi {sk_day(segment['first'])} ({km(segment['start'])}) a {sk_day(segment['last'])} ({km(segment['end'])})"
        if budget > 0 and not trips:
            problems.append(f"Tachometer {span} pribudol o {km(budget)}, ale v tomto období nie je žiadna jazda. Kam ste vtedy išli?")
            continue
        if budget == 0 and trips:
            problems.append(f"Jazdy {', '.join(t['id'] for t in trips)} sú {span}, kde sa tachometer nezmenil. Majú iný dátum?")
            continue
        fixed = [t for t in trips if t.get("km_confirmed")]
        free = [t for t in trips if not t.get("km_confirmed")]
        remaining = budget - sum(t["distance"] for t in fixed)
        if (not free and remaining != 0) or (free and remaining < len(free)):
            problems.append(f"Potvrdené kilometre jázd {', '.join(t['id'] for t in fixed)} nesedia s tachometrom {span} "
                            f"(k dispozícii je {km(budget)}). Ktorú vzdialenosť mám upraviť?")
            continue
        if not free:
            continue
        known = [t["approximate_km"] for t in free if t.get("approximate_km")]
        fallback = D(sum(known)) / len(known) if known else D(1)
        weights = [D(t.get("approximate_km") or fallback) for t in free]
        spare = remaining - len(free)
        quotas = [D(spare) * w / sum(weights) for w in weights]
        lengths = [1 + int(q) for q in quotas]
        order = sorted(range(len(free)), key=lambda i: (quotas[i] - int(quotas[i]), -i), reverse=True)
        for index in order[:remaining - sum(lengths)]:
            lengths[index] += 1
        for trip, distance in zip(free, lengths):
            trip["distance"] = distance
    return problems


def place(session, state, vehicle, frame):
    """Segments with the draft trips assigned and their kilometres distributed; returns (segments, problems)."""
    segments = segments_for(session, state, vehicle, frame)
    assign(segments, state["trips"])
    return segments, distribute(segments)


def chain(state, segments=None):
    """Odometers of draft trips: within each segment from its start reading (kept trips sit between segments)."""
    if segments is None:
        segments = [dict(start=state["start_odo"] or 0, trips=state["trips"])]
    for segment in segments:
        odo = segment["start"]
        for trip in segment["trips"]:
            trip["start_odo"], trip["end_odo"] = odo, odo + (trip.get("distance") or 0)
            odo = trip["end_odo"]


def sort_trips(state):
    state["trips"].sort(key=lambda t: (t["day"], t.get("order", 0)))
    for index, trip in enumerate(state["trips"]):
        trip["order"] = index


def spread(trips, days):
    """Give trips evenly spread days (distinct while there are enough days), keeping their order."""
    for position, trip in enumerate(trips):
        trip["day"] = days[min(len(days) - 1, ((position + 1) * len(days) - 1) // len(trips))].isoformat()


def segment_days(db, vehicle, segments, index):
    segment = segments[index]
    start = segment["first"] + timedelta(days=1) if index else segment["first"]
    return workdays(db, vehicle, min(start, segment["last"]), segment["last"])


def moved_text(trip, reason):
    return f"Jazdu {trip['id']} ({trip['destination']}) som presunul na {sk_day(trip['day'])}, {reason}."


def fill_gaps(db, vehicle, session, state, frame):
    """Move estimated-date trips into odometer segments left without a trip (e.g. after a user fixed a date)."""
    moves = []
    for _ in range(len(state["trips"])):
        segments = segments_for(session, state, vehicle, frame)
        assign(segments, state["trips"])
        empty = [i for i, s in enumerate(segments) if s["budget"] > 0 and not s["trips"] and not s["fixed"]]
        donors = [(abs(j - empty[0]), j) for j, s in enumerate(segments) if empty and len(s["trips"]) > 1
                  and any(not t["day_confirmed"] for t in s["trips"])]
        if not empty or not donors:
            break
        target, donor = empty[0], min(donors)[1]
        movable = [t for t in segments[donor]["trips"] if not t["day_confirmed"]]
        trip = movable[0] if donor > target else movable[-1]
        days = segment_days(db, vehicle, segments, target)
        trip["day"] = days[len(days) // 2].isoformat()
        sort_trips(state)
        moves.append(dict(id=trip["id"], text=moved_text(trip, f"aby tachometer sedel s tankovaním "
                                                         f"{sk_day(segments[target]['last'])} ({km(segments[target]['end'])})")))
    return moves


def pull_before_reading(db, vehicle, session, state, frame):
    """Estimated-date trips after the closing reading day move into the last days before it."""
    close = frame.close[0]
    late = [t for t in state["trips"] if t["day"] > close.isoformat() and not t["day_confirmed"]]
    if not late:
        return []
    tail = segments_for(session, state, vehicle, frame)[-1]
    spread(late, workdays(db, vehicle, min(tail["first"] + timedelta(days=1), close), close))
    sort_trips(state)
    return [dict(id=t["id"], text=moved_text(t, f"pred odčítanie tachometra {sk_day(close)}")) for t in late]


# --- Fuel -------------------------------------------------------------------------------------

def tank_opening(db, vehicle, month):
    """Tank level at the start of the month by the ledger (it replays the vehicle's whole history, so once a request)."""
    def compute():
        history = [row for row in ledger(db, vehicle) if row["day"] < month.isoformat()]
        return D(history[-1]["end_liters"]) if history else D(vehicle.initial_liters)
    return cached(db, ("opening", vehicle.id, month), compute)


def planning_rate(vehicle, state):
    """Norm consumption (l/100 km) of the planned trips, weighted by their approximate length."""
    rows = state["trips"] or state["routes"]
    weights = [(D(r.get("approximate_km") or r.get("distance") or 1) * D(r.get("count", 1)), r.get("category")) for r in rows]
    total = sum((w for w, _ in weights), D(0))
    if not total:
        return D(vehicle.outside_rate)
    return sum((w * norm(vehicle, c) for w, c in weights), D(0)) / total


def refuels(db, vehicle, session, state):
    """All refuels of the month as (day, odo or None, liters, measured level), saved ones and draft receipts."""
    rows = [(f.day, f.odo, D(f.liters), f.measured) for f in month_rows(db, vehicle, session.month)[1]]
    rows += [(date.fromisoformat(r["day"]), r.get("odo"), D(r["liters"]), None) for r in state["receipts"]
             if r.get("day") and r.get("liters")]
    return sorted(rows, key=lambda row: row[0])


def best_subset(amounts, low, high, target):
    """Indexes of `amounts` whose sum lies in [low, high], as close to `target` as possible (subset sum in 0.1 l).
    If no subset fits, the closest one, preferring to stay below `high` (a dry tank is worse than a too full one)."""
    def unit(value):
        return int((D(value) * 10).to_integral_value())

    def outside(total):
        return max(low - total, total - high), total > high
    steps = [unit(amount) for amount in amounts]
    low, high, target = unit(low), unit(high), unit(target)
    limit = high + max(steps, default=0)
    sums = {0: ()}
    for index, step in enumerate(steps):
        if len(sums) > 20000:   # absurd inputs (refuels far above the tank) must not eat CPU and memory
            break
        for total, picked in list(sums.items()):
            if total + step <= limit and total + step not in sums:
                sums[total + step] = picked + (index,)
    inside = [total for total in sums if low <= total <= high]
    best = min(inside, key=lambda total: (abs(total - target), total)) if inside else min(sums, key=outside)
    return list(sums[best])


def schedule_for_fuel(db, vehicle, session, state, segments):
    """Choose the days of estimated-date trips so that, by the norm, the tank never runs dry before a refuel and
    every refuel fits into the tank. Kilometres per trip stay as planned (they follow the destinations); only the
    order around refuels without an odometer changes. Returns the ids of trips whose day changed."""
    capacity = D(vehicle.capacity)
    use = lambda trip: D(trip.get("distance") or 0) * norm(vehicle, trip["category"]) / 100  # noqa: E731
    level = tank_opening(db, vehicle, session.month)
    all_refuels = refuels(db, vehicle, session, state)
    soft = [(day, liters, measured) for day, odo, liters, measured in all_refuels if odo is None]
    hard = [row for row in all_refuels if row[1] is not None]
    need = sum((use(t) for t in state["trips"]), D(0)) + sum(
        (D(s["budget"]) * D(vehicle.outside_rate) / 100 for s in segments if s["fixed"]), D(0))
    short = level + sum((row[2] for row in all_refuels), D(0)) < need
    reserve = D(0) if short else capacity * D("0.15")
    before = {t["id"]: t["day"] for t in state["trips"]}
    used_soft, used_hard = set(), set()
    for index, segment in enumerate(segments):
        for number, (day, odo, liters, measured) in enumerate(hard):
            if number not in used_hard and day == segment["first"] and odo == segment["start"]:
                used_hard.add(number)
                level = D(measured) if measured is not None else level + liters
        if segment["fixed"]:
            level -= D(segment["budget"]) * D(vehicle.outside_rate) / 100
            continue
        start = segment["first"] + timedelta(days=1) if index else segment["first"]
        dated = [t for t in segment["trips"] if t["day_confirmed"]]
        pool = [t for t in segment["trips"] if not t["day_confirmed"]]
        previous = start - timedelta(days=1)
        for number, (day, liters, measured) in enumerate(soft):
            if number in used_soft or not start <= day <= segment["last"]:
                continue
            used_soft.add(number)
            available = level - sum((use(t) for t in dated if previous.isoformat() < t["day"] <= day.isoformat()), D(0))
            # a slightly too full tank (within the ledger's tolerance) is fine, a dry one is not
            low = max(D(0), available + liters - capacity - tank_tolerance(vehicle))
            high = max(D(0), min(available, capacity))
            chosen = []
            if pool and day > previous:
                picked = best_subset([use(t) for t in pool], low, high, min(high, max(low, available - reserve)))
                chosen = [pool[i] for i in picked]
                if chosen:
                    spread(chosen, workdays(db, vehicle, previous + timedelta(days=1), day))
            pool = [t for t in pool if t not in chosen]
            level = available - sum((use(t) for t in chosen), D(0))
            level = D(measured) if measured is not None else level + liters
            previous = day
        if pool:
            spread(pool, workdays(db, vehicle, min(previous + timedelta(days=1), segment["last"]), segment["last"]))
        level -= sum((use(t) for t in pool), D(0)) + sum((use(t) for t in dated if t["day"] > previous.isoformat()), D(0))
    sort_trips(state)
    return [t["id"] for t in state["trips"] if before.get(t["id"]) != t["day"]]


def recalculate(db, vehicle, session, state, fresh=False, balance=True):
    """Re-run all backend checks on the draft schedule. Returns problems the assistant asks about.

    With `balance`, the days of estimated trips are re-chosen around refuels when the plan is `fresh` (just built)
    or the tank would run dry / overflow. The approval check passes balance=False: it only verifies."""
    sort_trips(state)
    state["moves"] = []
    if not state["trips"]:
        return []
    segments = None
    try:
        frame = check_month(db, vehicle, session, state)
        state["moves"] = pull_before_reading(db, vehicle, session, state, frame)
        state["moves"] += fill_gaps(db, vehicle, session, state, frame)
        early = [t["id"] for t in state["trips"] if t["day"] < vehicle.start_date.isoformat()]
        if early:
            raise Problem(f"Jazdy {', '.join(early)} sú pred začiatkom evidencie vozidla ({sk_day(vehicle.start_date)}). Opravte ich dátum.")
        segments, problems = place(session, state, vehicle, frame)
        if balance and not problems:
            chain(state, segments)
            if fresh or tank_balance(db, vehicle, session, state)["violations"]:
                moved = schedule_for_fuel(db, vehicle, session, state, segments)
                segments, problems = place(session, state, vehicle, frame)
                if moved and not fresh:
                    state["moves"].append(dict(id=moved[0], text=f"Dni jázd {', '.join(moved)} som posunul podľa tankovaní, "
                                               "aby nádrž nešla do mínusu a tankovanie sa zmestilo do nádrže."))
    except Problem as problem:
        problems = [str(problem)]
    chain(state, segments)
    for trip in state["trips"]:
        ratio = D(trip["distance"]) / trip["approximate_km"] if trip.get("approximate_km") and trip.get("distance") else D(1)
        trip["warning"] = (f"Vzdialenosť sa líši od odhadu {km(trip['approximate_km'])} o viac ako 30 %."
                           if abs(ratio - 1) > D("0.3") and not trip.get("km_confirmed") else None)
    unnamed = [t["id"] for t in state["trips"] if not (t.get("destination") or "").strip() or t["destination"] == NO_DESTINATION]
    if unnamed:
        problems = problems + [f"Jazdy {', '.join(unnamed)} nemajú cieľ. Doplňte, kam ste išli."]
    if not problems:
        state["fingerprint"] = cached(db, ("fingerprint", vehicle.id), lambda: planner.fingerprint(db, vehicle))
        state["stale"] = False
    return problems


def build_trips(db, vehicle, session, state):
    """Initial schedule from the routes Gemini understood: user-stated dates stay, the rest is spread.
    Returns (trip ids, how many trips were added to the description so every stretch has one)."""
    if not state["routes"]:
        raise Problem("Kde ste počas mesiaca približne jazdili? Napíšte napríklad „trikrát Košice, dvakrát Poprad“.")
    frame = check_month(db, vehicle, session, state)
    fixed, free, rounds = [], [], max(route["count"] for route in state["routes"])
    for route in state["routes"]:
        for day in route.get("days", [])[:route["count"]]:
            fixed.append((route, day))
    for repetition in range(rounds):
        for route in state["routes"]:
            if repetition < route["count"] - len(route.get("days", [])[:route["count"]]):
                free.append(route)
    if len(fixed) + len(free) > MAX_TRIPS:
        raise Problem(f"Návrh môže mať najviac {MAX_TRIPS} jázd. Znížte počet opakovaní.")

    by_length = sorted(state["routes"], key=lambda r: -(r.get("approximate_km") or 0))
    added = 0

    def add_longest():
        nonlocal added
        route = by_length[added % len(by_length)]
        route["count"] += 1
        free.append(route)
        added += 1

    # Every stretch between refuels without an odometer needs a trip, so there must be one more trip than such refuels.
    soft = sum(1 for row in refuels(db, vehicle, session, state) if row[1] is None)
    while soft and free and len(fixed) + len(free) <= soft and len(fixed) + len(free) < MAX_TRIPS:
        add_longest()

    def make(route, day, confirmed):
        return dict(id="", day=day, origin=route.get("origin") or state["origin"] or distances.HOME, destination=route["destination"],
                    category=route["category"], purpose=route.get("purpose") or "",
                    approximate_km=known_km(db, vehicle, dict(state, routes=[]), route["destination"], route.get("origin") or None)
                    or route.get("approximate_km"),
                    distance=0, day_confirmed=confirmed, km_confirmed=False, order=0)
    segments = segments_for(session, state, vehicle, frame)
    trips = sorted((make(route, day, True) for route, day in fixed), key=lambda t: t["day"])
    assign(segments, trips)
    counts = [len(s["trips"]) for s in segments]
    open_segments = [i for i, s in enumerate(segments) if s["budget"] > 0 and not s["fixed"]]
    empty = [i for i in open_segments if not counts[i]]
    if not open_segments:
        raise Problem("Všetky kilometre mesiaca už pokrývajú jazdy uložené v knihe. Nie je čo navrhnúť.")
    # every odometer segment needs a trip too
    while len(empty) > len(free) and len(fixed) + len(free) < MAX_TRIPS:
        add_longest()
    if len(empty) > len(free):
        source = "Tankovania a uložené jazdy" if frame.spans else "Tankovania"
        raise Problem(f"{source} rozdeľujú mesiac na {len(open_segments)} úsekov s jazdou, "
                      f"ale opis obsahuje iba {len(fixed) + len(free)} jázd. Kam ste ešte išli?")
    extra = [0] * len(segments)
    for index in empty:
        extra[index] = 1
    for _ in range(len(free) - len(empty)):
        candidates = [i for i, s in enumerate(segments) if not s["fixed"] and s["budget"] > counts[i] + extra[i]]
        if not candidates:
            raise Problem("Počet jázd je vyšší ako počet najazdených kilometrov. Skontrolujte opis.")
        index = max(candidates, key=lambda i: (D(segments[i]["budget"]) / (counts[i] + extra[i] + 1), -i))
        extra[index] += 1
    # Match longer routes with segments that need more kilometres per trip, then spread dates evenly.
    slots = sorted(((D(segments[i]["budget"]) / (counts[i] + extra[i]), i) for i in range(len(segments))
                    for _ in range(extra[i])), key=lambda slot: (-slot[0], slot[1]))
    ranked = sorted(range(len(free)), key=lambda i: (-(free[i].get("approximate_km") or 0), i))
    chosen = {index: [] for index in range(len(segments))}
    for (_, index), occurrence in zip(slots, ranked):
        chosen[index].append(occurrence)
    for index, picked in chosen.items():
        if picked:
            new = [make(free[occurrence], "", False) for occurrence in sorted(picked)]
            spread(new, segment_days(db, vehicle, segments, index))
            trips += new
    trips.sort(key=lambda t: (t["day"], not t["day_confirmed"]))
    state["counter"]["J"] = 0
    for trip in trips:
        trip["id"] = next_id(state, "J")
    state["trips"] = trips
    return [trip["id"] for trip in trips], added


# --- Operations -------------------------------------------------------------------------------

def apply_operation(db, vehicle, session, state, op):
    """Apply one allowed change to a concrete draft row. Returns the affected row ids."""
    first, last = month_bounds(session)
    if op.day and not first <= op.day <= last:
        raise Problem(f"Dátum {sk_day(op.day)} nie je vo vybranom mesiaci.")
    if op.day and op.day < vehicle.start_date:
        raise Problem("Dátum predchádza začiatku evidencie vozidla.")
    if op.action == "set_replace":
        info = month_trips(db, vehicle, session.month)
        state["replace_existing"] = bool(op.replace and info["replaceable"])
        return ["existing"]
    if op.action == "set_odometers":
        for key in ("start_odo", "end_odo"):
            if getattr(op, key) is not None:
                state[key] = getattr(op, key)
            if key in op.clear:
                state[key] = None
        if op.end_day:
            if op.end_day < max(first, vehicle.start_date) or op.end_day > min(last + timedelta(days=31), today()):
                raise Problem(f"Dátum odčítania tachometra musí byť od {sk_day(max(first, vehicle.start_date))} "
                              f"do {sk_day(min(last + timedelta(days=31), today()))}.")
            state["end_day"] = None if op.end_day == last else op.end_day.isoformat()
        if "end_day" in op.clear:
            state["end_day"] = None
        return ["odo"]
    if op.action in {"update_trip", "delete_trip"}:
        trip = find(state, "trips", op.row)
        if op.action == "delete_trip":
            state["trips"].remove(trip)
            return []
        if op.day:
            trip.update(day=op.day.isoformat(), day_confirmed=True, order=-1 if op.day.isoformat() < trip["day"] else 10**6)
        if op.destination is not None:
            trip["destination"] = op.destination
            trip["approximate_km"] = known_km(db, vehicle, state, op.destination) or trip.get("approximate_km")
        for key in ("origin", "category", "purpose"):
            if getattr(op, key) is not None:
                trip[key] = getattr(op, key)
        if op.km:
            trip.update(distance=op.km, km_confirmed=True)
        if "km" in op.clear:
            trip["km_confirmed"] = False
        return [trip["id"]]
    if op.action == "add_trip":
        if len(state["trips"]) >= MAX_TRIPS:
            raise Problem(f"Koncept môže mať najviac {MAX_TRIPS} jázd.")
        if not op.destination:
            raise Problem("Pri novej jazde uveďte cieľ.")
        day = op.day
        if not day:  # no date given: first free workday, flagged as an estimate
            used = {t["day"] for t in state["trips"]}
            options = workdays(db, vehicle, max(first, vehicle.start_date), min(last, closing(db, vehicle, session, state)[0]))
            day = next((d for d in options if d.isoformat() not in used), options[len(options) // 2])
        trip = dict(id=next_id(state, "J"), day=day.isoformat(), origin=op.origin or state["origin"] or distances.HOME,
                    destination=op.destination,
                    category=op.category or ("mesto" if distances.is_city(op.destination) else "mimo mesto"), purpose=op.purpose or "",
                    approximate_km=op.km or known_km(db, vehicle, state, op.destination), distance=op.km or 1,
                    day_confirmed=bool(op.day), km_confirmed=bool(op.km), order=10**6)
        state["trips"].append(trip)
        return [trip["id"]]
    if op.action == "delete_file":
        item = next((f for f in state["files"] if f["id"] == op.row), None)
        if not item:
            raise Problem("Súbor sa v koncepte nenašiel.")
        state["receipts"] = [r for r in state["receipts"] if r.get("file_id") != item["id"]]
        remove_file(db, state, item["id"])
        return []
    if op.action == "add_receipt":
        if len(state["receipts"]) >= 60:
            raise Problem("Koncept môže mať najviac 60 bločkov.")
        receipt = dict(id=next_id(state, "B"), file_id=None, file_name=None, source="ručne",
                       duplicate_ok=False, unreadable=[], day=op.day.isoformat() if op.day else None,
                       station=op.station or "", liters=decimal_text(op.liters, "0.001"), gross=decimal_text(op.gross, "0.01"),
                       currency=op.currency or "EUR", vat=decimal_text(op.vat, "0.01") if op.vat is not None else default_vat(op.day and op.day.isoformat()),
                       odo=op.odo)
        state["receipts"].append(receipt)
        return [receipt["id"]]
    receipt = find(state, "receipts", op.row)
    if op.action == "delete_receipt":
        state["receipts"].remove(receipt)
        if receipt.get("file_id") and not any(r.get("file_id") == receipt["file_id"] for r in state["receipts"]):
            remove_file(db, state, receipt["file_id"])
        return []
    if op.action == "dismiss_duplicate":
        receipt["duplicate_ok"] = True
        return [receipt["id"]]
    changes = {}
    if op.day:
        changes["day"] = op.day.isoformat()
    if op.station is not None:
        changes["station"] = op.station
    if op.liters is not None:
        changes["liters"] = decimal_text(op.liters, "0.001")
    if op.gross is not None:
        changes["gross"] = decimal_text(op.gross, "0.01")
    if op.vat is not None:
        changes["vat"] = decimal_text(op.vat, "0.01")
    if op.currency:
        changes["currency"] = op.currency
    if op.odo is not None:
        changes["odo"] = op.odo
    for key in op.clear:
        if key in {"odo", "day", "liters", "gross"}:
            changes[key] = None
    changes = {key: value for key, value in changes.items() if receipt.get(key) != value}
    if changes:
        receipt.update(changes)
        receipt["unreadable"] = [name for name in receipt.get("unreadable", []) if name not in changes]
        receipt["duplicate_ok"] = False
    return [receipt["id"]]


def wire_operation(op):
    """Convert Gemini's proposed operation into the validated operation model."""
    values = op.model_dump(exclude_none=True)
    for key, places in (("liters", 3), ("gross", 2), ("vat", 2)):
        if key in values:
            values[key] = round(values[key], places)
    if "day" in values:
        try:
            values["day"] = date.fromisoformat(values["day"])
        except ValueError:
            raise Problem(f"Dátum „{values['day']}“ sa nedá použiť.")
    return Operation.model_validate(values)


# --- View -------------------------------------------------------------------------------------

def liters(value):
    return f"{rounded(D(value), '0.1')} l".replace(".", ",")


def tank_balance(db, vehicle, session, state):
    """Simulate the fuel tank over the draft month exactly like the ledger: the opening level from the logbook,
    every receipt is added (the car is refuelled) and each trip consumes its norm. Flags refuels that would not
    fit into the tank and a tank that would run dry, which usually means wrong litres or missing trips."""
    opening = tank_opening(db, vehicle, session.month)
    kept, fuels = saved_rows(db, vehicle, session, state)
    events = []  # (sort key, row id, consumed litres or None, refuelled litres or None, measured level after refuel)
    # rows saved in the logbook get keys "kniha-j<id>" / "kniha-t<id>" so the draft can show them read-only
    for trip in kept:
        events.append(((trip.day.isoformat(), trip.end_odo, 0, "", trip.id), f"kniha-j{trip.id}",
                       trip.rate * (trip.end_odo - trip.start_odo) / 100, None, None))
    for fuel in fuels:
        events.append(((fuel.day.isoformat(), fuel.odo if fuel.odo is not None else 10**12, 1, fuel.clock, fuel.id),
                       f"kniha-t{fuel.id}", None, fuel.liters, fuel.measured))
    for index, trip in enumerate(state["trips"]):
        events.append(((trip["day"], trip.get("end_odo") or 0, 0, "", index), trip["id"],
                       norm(vehicle, trip["category"]) * D(trip.get("distance") or 0) / 100, None, None))
    for index, receipt in enumerate(state["receipts"]):
        if receipt.get("day") and receipt.get("liters"):
            odo = receipt["odo"] if receipt.get("odo") is not None else 10**12
            events.append(((receipt["day"], odo, 1, "", 10**6 + index), receipt["id"], None, D(receipt["liters"]), None))
    level, purchased, consumed, rows, warnings, days, violations = opening, D(0), D(0), {}, [], {}, 0
    tolerance, planned = tank_tolerance(vehicle), bool(state["trips"])
    for key, row_id, use, refuel, measured in sorted(events, key=lambda event: event[0]):
        before = level
        label = " (v knihe)" if row_id.startswith("kniha-") else f" ({row_id})"
        if use is not None:
            level -= use
            consumed += use
        else:
            if before < -tolerance:
                violations += 1
                if planned:
                    warnings.append(f"Pred tankovaním {sk_day(key[0])}{label} by nádrž podľa normy klesla na "
                                    f"{liters(before)}. Chýba skoršie tankovanie alebo sú kilometre privysoké.")
            level += refuel
            purchased += refuel
            if level > vehicle.capacity + tolerance:
                violations += 1
                if planned:
                    warnings.append(f"Tankovanie {sk_day(key[0])}{label}: pred tankovaním {liters(before)} + "
                                    f"{liters(refuel)} = {liters(level)}, čo je viac ako nádrž {liters(vehicle.capacity)}. "
                                    "Skontrolujte litre na bločku alebo doplňte jazdy pred týmto tankovaním.")
            if measured is not None:
                level = D(measured)  # measured tank level after refuel, as in the ledger
        rows[row_id] = dict(before=rounded(before, "0.1"), after=rounded(level, "0.1"),
                            used=rounded(use, "0.1") if use is not None else None)
        days[key[0]] = rounded(level, "0.1")
    per_refuel(events, rows, kept, state, km_since_refuel(db, vehicle, session.month))
    if planned and level < -tolerance:
        warnings.append(fuel_shortage_text(vehicle, state, kept, opening, purchased, consumed, level))
    return dict(opening=rounded(opening, "0.1"), purchased=rounded(purchased, "0.1"), consumed=rounded(consumed, "0.1"),
                closing=rounded(level, "0.1"), capacity=rounded(vehicle.capacity, "0.1"), planned=planned,
                rows=rows, days=days, warnings=warnings, violations=violations)


def km_since_refuel(db, vehicle, month):
    """Kilometre z knihy jázd od posledného tankovania pred mesiacom po jeho začiatok (patria do prvého okna)."""
    last = db.scalar(select(Fuel.day).where(Fuel.vehicle_id == vehicle.id, Fuel.day < month).order_by(Fuel.day.desc()).limit(1))
    if last is None:
        return None   # bez skoršieho tankovania sa prvé okno nedá uzavrieť
    return db.scalar(select(func.coalesce(func.sum(Trip.end_odo - Trip.start_odo), 0))
                     .where(Trip.vehicle_id == vehicle.id, Trip.day > last, Trip.day < month))


def per_refuel(events, rows, kept, state, km_before):
    """Priemerná spotreba v l/100 km medzi tankovaniami (ako stĺpec sadzby v pôvodnom Exceli): litre natankované
    pri tankovaní / kilometre od predošlého tankovania. Pri každej jazde okna je ten istý priemer; jazdy po poslednom
    tankovaní mesiaca priemer nemajú (okno ešte nie je uzavreté)."""
    distance = {f"kniha-j{t.id}": t.end_odo - t.start_odo for t in kept}
    distance.update({t["id"]: t.get("distance") or 0 for t in state["trips"]})
    window, carried = [], km_before
    for _key, row_id, use, refuel, _measured in sorted(events, key=lambda event: event[0]):
        if use is not None:
            window.append(row_id)
            continue
        if carried is None:   # prvé tankovanie bez známeho predošlého: priemer by bol nepresný
            km_total, average = sum(distance.get(i, 0) for i in window), None
        else:
            km_total = carried + sum(distance.get(i, 0) for i in window)
            average = rounded(D(refuel) * 100 / km_total, "0.1") if km_total else None
        carried = 0
        rows[row_id].update(km=km_total, per100=average)
        for trip_id in window:
            rows[trip_id].update(per100=average, window=row_id)
        window = []


def fuel_shortage_text(vehicle, state, kept, opening, purchased, consumed, level):
    rate = planning_rate(vehicle, state)
    if rate <= 0:
        return f"Na konci mesiaca by nádrž podľa normy bola na {liters(level)}. Chýba tankovanie alebo sú kilometre privysoké."
    available = opening + purchased
    month_km = sum(t.get("distance") or 0 for t in state["trips"]) + sum(t.end_odo - t.start_odo for t in kept)
    return (f"Palivo na celý mesiac nestačí: na {km(month_km)} treba podľa normy {liters(consumed)}, k dispozícii bolo "
            f"{liters(opening)} v nádrži + {liters(purchased)} natankovaných = {liters(available)}. Na konci mesiaca by "
            f"nádrž bola na {liters(level)}. Pri norme {str(rounded(rate, '0.1')).replace('.', ',')} l/100 km vystačí na "
            f"asi {km(int(available * 100 / rate))}. Skontrolujte, či nechýba bloček z tankovania, či sedí koncový stav "
            "tachometra a norma spotreby vozidla. Počas mesiaca jazdy rozdeľujem tak, aby nádrž pred tankovaním do mínusu nešla.")


def tank_text(tank):
    text = (f"Nádrž: začiatok {liters(tank['opening'])}, natankované +{liters(tank['purchased'])}"
            + (f", spotreba podľa normy −{liters(tank['consumed'])}, koniec {liters(tank['closing'])}." if tank["planned"] else "."))
    return " ".join([text, *tank["warnings"]])


def view(db, vehicle, session, state=None, error=None):
    state = {**blank_state(), **deepcopy(state or session.state)}
    draft = session.status == "draft"
    problems = receipt_checks(db, vehicle, session, state) if draft else []
    kept, fuels = saved_rows(db, vehicle, session, state) if draft else ([], [])
    kept_km = sum(t.end_odo - t.start_odo for t in kept)
    total = sum(t.get("distance") or 0 for t in state["trips"]) + kept_km
    close_day, close_odo, estimated = closing(db, vehicle, session, state)
    expected = (close_odo - state["start_odo"]) if state["start_odo"] is not None and close_odo is not None else None
    can_approve = (draft and not problems and bool(state["trips"])
                   and not state["problems"] and not state["stale"] and expected == total)
    first, last = month_bounds(session)
    month_days = [dict(day=day, holiday=day in holidays(db, vehicle), weekend=day.weekday() >= 5,
                       before_start=day < vehicle.start_date, after_reading=day > close_day)
                  for day in (first + timedelta(days=i) for i in range((last - first).days + 1))]
    files = [{**item, "url": f"/vehicle-logbook/api/assistant/{session.id}/files/{item['id']}"} for item in state["files"]]
    return json_value(dict(
        id=session.id, vehicle_id=vehicle.id, month=session.month.isoformat()[:7], status=session.status,
        version=session.version, result=session.result, error=error,
        start_odo=state["start_odo"], end_odo=state["end_odo"], end_day=state.get("end_day"),
        closing=dict(day=close_day, odo=close_odo, estimated=estimated), days=month_days,
        origin=state["origin"], routes=state["routes"],
        expected_start=expected_start(db, vehicle, session.month), files=files, receipts=state["receipts"],
        receipt_problems=problems, trips=state["trips"],
        problems=state["problems"], stale=state["stale"], messages=state["messages"], highlight=state["highlight"],
        existing={k: v for k, v in month_trips(db, vehicle, session.month).items() if k != "ids"},
        tank=tank_balance(db, vehicle, session, state) if draft else None,
        saved=dict(km=kept_km, trips=[dict(key=f"kniha-j{t.id}", day=t.day, origin=t.origin, destination=t.destination,
                                         purpose=t.purpose, category=t.category, distance=t.end_odo - t.start_odo,
                                         start_odo=t.start_odo, end_odo=t.end_odo, estimated=bool(t.draft_id)) for t in kept],
                   fuels=[dict(key=f"kniha-t{f.id}", day=f.day, station=f.station, liters=f.liters, gross=f.gross, odo=f.odo,
                               note=f.note) for f in fuels]),
        replace_existing=state.get("replace_existing", False),
        distance=expected, planned=total, can_approve=can_approve,
        assistant=dict(configured=gemini.configured(), model=gemini.model_name()),
        limits=dict(file_mb=MAX_FILE_BYTES // 1_000_000, files=MAX_FILES, message=MAX_MESSAGE,
                    types=sorted(set(ALLOWED.values())))))


def say(state, role, text, changed=()):
    state["messages"].append(dict(role=role, text=text[:4000], at=now().isoformat(timespec="seconds"),
                                  changed=list(changed)))
    state["messages"] = state["messages"][-MAX_MESSAGES:]


def build_balanced(db, vehicle, session, state):
    """build_trips, then while the tank would still run dry before a refuel or a refuel would overflow it, split the
    trip that needs the most fuel (one more occurrence of its destination) and plan again. A split that does not
    reduce the problems (e.g. they come from fixed odometer readings) is undone. Returns (trip ids, trips added)."""
    changed, added = build_trips(db, vehicle, session, state)
    problems = recalculate(db, vehicle, session, state, fresh=True)
    for _ in range(8):
        violations = tank_balance(db, vehicle, session, state)["violations"]
        movable = sorted((t for t in state["trips"] if not t["day_confirmed"] and not t["km_confirmed"]),
                         key=lambda t: -t["distance"])
        route = next((r for t in movable for r in state["routes"] if r["destination"] == t["destination"]), None)
        if problems or not violations or route is None:
            break
        attempt = deepcopy(state)
        next(r for r in attempt["routes"] if r["destination"] == route["destination"])["count"] += 1
        attempt_changed, _ = build_trips(db, vehicle, session, attempt)
        problems = recalculate(db, vehicle, session, attempt, fresh=True)
        if problems or tank_balance(db, vehicle, session, attempt)["violations"] >= violations:
            break
        state.clear()
        state.update(attempt)
        changed, added = attempt_changed, added + 1
    return changed, added


def auto_plan(db, vehicle, session, state):
    """Create the first schedule as soon as the month is described (start, end and routes). Returns (ids, text)."""
    if state["trips"] or not state["routes"] or state["start_odo"] is None or state["end_odo"] is None:
        return [], ""
    try:
        changed, added = build_balanced(db, vehicle, session, state)
    except Problem as problem:
        return [], str(problem)
    text = (f"Pripravil som návrh na celý mesiac, počet jázd: {len(state['trips'])}. Odhady sú označené; konkrétne dni alebo "
            "kilometre mi môžete opraviť v chate alebo kliknutím na jazdu.")
    if added:
        text += (f" Aby pred každým tankovaním bola jazda a palivo sa zmestilo do nádrže, pridal som {added} "
                 f"{plural(added, 'jazdu', 'jazdy', 'jázd')} k najdlhším cieľom.")
    return changed, text


def refresh(db, vehicle, session, state):
    receipt_checks(db, vehicle, session, state)
    state["problems"] = recalculate(db, vehicle, session, state) if state["trips"] else []
    return state["problems"]


# --- Endpoints --------------------------------------------------------------------------------

@router.get("/assistant/config")
def config(user=Member):
    return dict(configured=gemini.configured(), model=gemini.model_name(),
                limits=dict(file_mb=MAX_FILE_BYTES // 1_000_000, files=MAX_FILES, types=sorted(set(ALLOWED.values()))))


@router.post("/vehicles/{vehicle_id}/assistant")
def open_session(vehicle_id: int, data: SessionIn, db=Db, user=Member):
    vehicle = vehicle_for(db, user, vehicle_id, True)
    month = month_of(data.month)
    if month >= month_of(today()):
        fail("Mesačný sprievodca je určený pre skončený mesiac. Vyberte predchádzajúci mesiac.", 409)
    if next_month(month) <= vehicle.start_date:   # also stops drafts (with uploads) for arbitrary past months
        fail("Vybraný mesiac predchádza začiatku evidencie vozidla.", 409)
    session = db.scalar(select(AssistantSession).where(AssistantSession.vehicle_id == vehicle_id,
        AssistantSession.user_id == user.id, AssistantSession.month == month))
    if not session:
        state = blank_state()
        # the opening reading continues the logbook; the closing one is known when the next month already has trips
        state["start_odo"] = expected_start(db, vehicle, month)
        following = db.scalar(select(Trip).where(Trip.vehicle_id == vehicle.id, Trip.day >= next_month(month))
                              .order_by(Trip.day, Trip.end_odo, Trip.id).limit(1))
        if following:
            state["end_odo"] = following.start_odo
        known = f"Začiatok mesiaca podľa knihy jázd je {km(state['start_odo'])}" + (
            f" a koniec {km(state['end_odo'])}." if state["end_odo"] is not None else ".")
        say(state, "assistant", f"Dobrý deň. {known} Nahrajte bločky z tankovania za tento mesiac a napíšte, "
            + ("" if state["end_odo"] is not None else "aký bol stav tachometra na konci mesiaca (a ktorý deň ste ho "
               "odčítali, ak nie posledný) a ")
            + "kde ste približne jazdili. Napríklad: „Koniec 13 280 km, odčítané 3. 11., trikrát Košice a dvakrát "
            f"Poprad.“ Návrh jázd na celý mesiac pripravím sám. Jazdy počítam s východiskom {distances.HOME}; ak ste "
            "štartovali inde, napíšte mi to.")
        session = AssistantSession(id=str(uuid4()), vehicle_id=vehicle_id, user_id=user.id, month=month,
                                   status="draft", version=1, state=state)
        loaded = saved_text(db, vehicle, session, state)
        if loaded:
            say(state, "assistant", loaded)
        info = month_trips(db, vehicle, month)
        if info["replaceable"]:
            say(state, "assistant", existing_text(info, month))
        db.add(session)
        db.commit()
    return view(db, vehicle, session)


@router.get("/assistant/{session_id}")
def get_session(session_id: str, db=Db, user=Member):
    session, vehicle, _ = session_for(db, user, session_id, lock=False)
    return view(db, vehicle, session)


@router.delete("/assistant/{session_id}")
def discard(session_id: str, db=Db, user=Member):
    session, vehicle, _ = session_for(db, user, session_id)
    db.execute(delete(AssistantFile).where(AssistantFile.session_id == session.id))
    db.delete(session)
    db.commit()
    return {"ok": True}


def sniff(content):
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content[4:8] == b"ftyp" and content[8:12] in {b"heic", b"heix", b"hevc", b"heim", b"heis"}:
        return "image/heic"
    if content[4:8] == b"ftyp" and content[8:12] in {b"mif1", b"msf1"}:
        return "image/heif"
    return None


@router.post("/assistant/{session_id}/files")
def upload(session_id: str, file: UploadFile = File(), db=Db, user=Member):
    session, vehicle, state = session_for(db, user, session_id)
    require_draft(session)
    if len(state["files"]) >= MAX_FILES:
        fail(f"Ku konceptu možno nahrať najviac {MAX_FILES} súborov.")
    content = file.file.read(MAX_FILE_BYTES + 1)
    if len(content) > MAX_FILE_BYTES:
        fail(f"Súbor presahuje {MAX_FILE_BYTES // 1_000_000} MB. Zmenšite fotografiu alebo PDF.")
    mime = sniff(content)
    if not mime:
        fail("Podporované sú iba fotografie JPG, PNG, WebP, HEIC a súbory PDF.")
    digest = hashlib.sha256(content).hexdigest()
    if db.scalar(select(AssistantFile.id).where(AssistantFile.session_id == session.id, AssistantFile.sha256 == digest)):
        fail("Tento súbor už je v koncepte nahraný.", 409)
    name = (file.filename or "bloček").replace("/", "_").replace("\\", "_")[-200:]
    stored = AssistantFile(id=str(uuid4()), session_id=session.id, filename=name, mime=mime, size=len(content),
                           sha256=digest, data=content)
    db.add(stored)
    state["files"].append(dict(id=stored.id, name=name, mime=mime, size=len(content), status="pending", error=None))
    store(session, state)
    db.commit()
    return view(db, vehicle, session)


@router.get("/assistant/{session_id}/files/{file_id}")
def download(session_id: str, file_id: str, db=Db, user=Member):
    session, vehicle, _ = session_for(db, user, session_id, lock=False)
    stored = db.get(AssistantFile, file_id)
    if not stored or stored.session_id != session.id:
        fail("Súbor sa nenašiel.", 404)
    extension = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "application/pdf": "pdf"}.get(stored.mime, "heic")
    return Response(stored.data, media_type=stored.mime, headers={
        "Content-Disposition": f'inline; filename="blocek.{extension}"',
        "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'self'",
        "X-Content-Type-Options": "nosniff"})


def run_gemini_step(db, session, vehicle, state, work):
    """Run a Gemini-backed step; on failure keep the draft and report the problem in the chat."""
    try:
        return work()
    except gemini.AssistantError as error:
        calls = state["gemini_calls"]
        state = deepcopy(session.state)  # last stored draft; partial work of this step is dropped
        state["gemini_calls"] = max(calls, state["gemini_calls"])
        say(state, "error", str(error))
        store(session, state)
        db.commit()
        return view(db, vehicle, session, error=str(error))


@router.post("/assistant/{session_id}/extract")
def extract(session_id: str, db=Db, user=Member):
    session, vehicle, state = session_for(db, user, session_id)
    require_draft(session)

    def work():
        notes, changed = extract_pending(db, session, state, retry=True)
        planned, plan_text = auto_plan(db, vehicle, session, state)
        refresh(db, vehicle, session, state)
        if plan_text:
            notes.append(plan_text)
        changed += planned
        if changed:
            problems = receipt_checks(db, vehicle, session, state)
            count = len([c for c in changed if c.startswith("B")])
            text = f"Spracoval som {count} {plural(count, 'bloček', 'bločky', 'bločkov')}. " \
                   "Skontrolujte ich v prehľade mesiaca pri danom dni; opraviť ich môžete kliknutím alebo mi napíšte."
            if problems:
                text += " Treba doplniť: " + " ".join(problems[:5])
            say(state, "assistant", text + (" " + " ".join(notes) if notes else ""), changed)
        elif notes:
            say(state, "assistant", " ".join(notes))
        store(session, state, changed)
        db.commit()
        return view(db, vehicle, session)
    return run_gemini_step(db, session, vehicle, state, work)


@router.post("/assistant/{session_id}/messages")
def message(session_id: str, data: MessageIn, db=Db, user=Member):
    session, vehicle, state = session_for(db, user, session_id)
    require_draft(session)
    say(state, "user", data.text)
    store(session, state)
    db.commit()  # the user's message survives any Gemini failure

    def work():
        notes, changed = extract_pending(db, session, state)
        if state["gemini_calls"] >= MAX_GEMINI_CALLS:
            raise gemini.AssistantError("Koncept dosiahol limit volaní Gemini. Zvyšné údaje upravte ručne v tabuľkách.")
        state["gemini_calls"] += 1
        spend(session.user_id)
        receipt_checks(db, vehicle, session, state)
        history = [m for m in state["messages"][:-1] if m["role"] in {"user", "assistant"}][-HISTORY:]
        result = gemini.interpret(context(db, vehicle, session, state), history, data.text)
        answer = list(notes)
        if result.start_odo is not None or result.end_odo is not None or result.end_day:
            try:
                end_day = date.fromisoformat(result.end_day) if result.end_day else None
                apply_operation(db, vehicle, session, state, Operation(action="set_odometers", start_odo=result.start_odo,
                                                                       end_odo=result.end_odo, end_day=end_day))
                changed.append("odo")
                if end_day:
                    answer.append(closing_text(db, vehicle, session, state))
            except Problem as problem:
                answer.append(str(problem))
            except ValueError:
                answer.append("Stav tachometra mimo povoleného rozsahu som nezapísal. Napíšte ho, prosím, znova.")
        if result.origin:
            state["origin"] = result.origin.strip()[:150]
        if result.routes is not None:
            routes = clean_routes(db, vehicle, session, state, result.routes)
            if routes:
                state["routes"] = routes
                changed.append("routes")
                if result.routes_invented and fit_counts(db, vehicle, session, state):
                    answer.append("Počty jázd som prispôsobil najazdeným kilometrom: " + route_list(state) + ".")
        for proposal in result.operations[:20]:
            try:
                changed += apply_operation(db, vehicle, session, state, wire_operation(proposal))
            except (Problem, ValueError) as error:
                answer.append(f"Zmenu {proposal.row or ''} som neurobil: {problem_text(error)}")
        own_routes = True
        if result.create_plan:
            own_routes, notes = plan_request(db, vehicle, session, state, changed)
            answer += notes
        planned, plan_text = auto_plan(db, vehicle, session, state)
        changed += planned
        answer.append(plan_text)
        if result.create_plan and not state["trips"] and not plan_text:
            answer.append(missing_for_plan(state))
        problems = refresh(db, vehicle, session, state)
        answer += [move["text"] for move in state["moves"]]
        changed += [move["id"] for move in state["moves"]]
        answer += [p for p in problems if p not in answer]
        if state["trips"] and edited_rows(changed):
            answer += [w for w in tank_balance(db, vehicle, session, state)["warnings"] if w not in answer]
        answer.append(odometer_question(state))
        # the model's own words go first, unless they named destinations it did not return (history fallback)
        reply = honest_reply(result.reply) if own_routes else ""
        answer.insert(0, reply)
        say(state, "assistant", "\n".join(dict.fromkeys(a for a in answer if a)) or "Rozumiem.", changed)
        store(session, state, changed)
        db.commit()
        return view(db, vehicle, session)
    return run_gemini_step(db, session, vehicle, state, work)


CLAIM_WORDS = ("pripravil", "vytvoril", "vygeneroval", "pregeneroval", "upravil", "prepracoval", "rozložil", "rozdelil",
               "presunul", "prepočítal", "opravil", "zmenil", "uložil", "pridal", "zapísal",
               "pregenerujem", "vygenerujem", "prepočítam", "pripravím", "vytvorím", "upravím", "rozložím", "rozdelím",
               "presuniem", "prepracujem", "opätovne odosielam", "je pripravený", "je pripravená", "je hotový", "je hotová",
               "bol vytvorený", "bol upravený", "boli upravené", "boli rozložené")
CLAIM = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in CLAIM_WORDS) + r")\w*", re.IGNORECASE)


def honest_reply(text):
    """Drop sentences in which the model claims it changed the schedule itself; the system reports the real result."""
    sentences = re.split(r"(?<=[.!?])\s+", (text or "").strip())
    kept = [sentence for sentence in sentences if sentence and not CLAIM.search(sentence)]
    return " ".join(kept) if kept else ("Rozumiem." if text else "")


def edited_rows(changed):
    """Whether receipts (B…) or trips (J…) changed."""
    return any(item[:1] in {"B", "J"} for item in changed)


def route_list(state):
    return ", ".join(f"{r['destination']} {r['count']}×" for r in state["routes"])


def plan_request(db, vehicle, session, state, changed):
    """The user asked for a (new) plan. An unedited schedule is dropped so it is built again; without any
    destinations they come from the vehicle's history. Returns (the model's reply still applies, notes)."""
    if state["trips"] and state["routes"] and not edited_rows(changed):
        state["trips"] = []
    if state["routes"] or state["trips"]:
        return True, []
    state["routes"] = routes_from_history(db, vehicle, session, state)
    if not state["routes"]:
        return True, []
    changed.append("routes")
    return False, [f"Ciele som vybral z častých jázd tohto vozidla: {route_list(state)}. Ide o odhad, môžete ho upraviť."]


def missing_for_plan(state):
    missing = []
    if state["start_odo"] is None or state["end_odo"] is None:
        missing.append("stav tachometra na začiatku a na konci mesiaca")
    if not state["routes"]:
        missing.append("aspoň približne kam ste jazdili (alebo napíšte „navrhni ciele sám“)")
    return f"Návrh jázd zatiaľ nie je vytvorený. Potrebujem ešte: {' a '.join(missing)}." if missing else ""


def problem_text(error):
    if hasattr(error, "errors"):
        return "hodnota je mimo povoleného rozsahu."
    return str(error)


def clean_routes(db, vehicle, session, state, routes):
    first, last = month_bounds(session)
    result = []
    for route in routes[:30]:
        destination = (route.destination or "").strip()[:150]
        if not destination or not 1 <= route.count <= 60:
            continue
        days = []
        for value in route.days:
            try:
                day = date.fromisoformat(value)
            except ValueError:
                continue
            if first <= day <= last:
                days.append(day.isoformat())
        approximate = route.approximate_km if route.approximate_km and 1 <= route.approximate_km <= 5000 else None
        origin = (route.origin or "").strip()[:150]
        result.append(dict(destination=destination, origin=origin, count=route.count,
                           approximate_km=known_km(db, vehicle, dict(routes=[], origin=state["origin"]), destination,
                                                   origin or None) or approximate,
                           category="mesto" if distances.is_city(destination) else route.category,
                           purpose=(route.purpose or "").strip()[:150], days=sorted(days)))
    return result


def km_to_plan(db, vehicle, session, state):
    """Kilometres of the month not covered by trips kept in the logbook (None until both odometers are known)."""
    close_odo = closing(db, vehicle, session, state)[1]
    if state["start_odo"] is None or close_odo is None:
        return None
    return close_odo - state["start_odo"] - sum(t.end_odo - t.start_odo for t in saved_rows(db, vehicle, session, state)[0])


def book_context(db, vehicle, session, state):
    """Rows already saved in the logbook for the month, read-only for the assistant."""
    trips, fuels = saved_rows(db, vehicle, session, state)
    return dict(
        tankovania=[dict(datum=f.day, stanica=f.station, litre=f.liters, suma=f.gross, tachometer=f.odo, zdroj=f.note)
                    for f in fuels][:60],
        tankovania_litre_spolu=sum((f.liters for f in fuels), D(0)),
        jazdy=[dict(datum=t.day, odkial=t.origin, ciel=t.destination, ucel=t.purpose, typ=t.category,
                    tachometer_od=t.start_odo, tachometer_do=t.end_odo, km=t.end_odo - t.start_odo,
                    mesacny_odhad=bool(t.draft_id)) for t in trips][:80],
        km_ulozenych_jazd=sum(t.end_odo - t.start_odo for t in trips), km_na_navrh=km_to_plan(db, vehicle, session, state))


def destination_history(db, vehicle, month):
    """Most frequent destinations of this vehicle in the six months before, so the assistant can invent a plausible
    month. The usual length is the same estimate the planner uses (history_km)."""
    since = month_of(month - timedelta(days=183))
    rows = db.execute(select(Trip.destination, Trip.origin, Trip.category, func.count())
                      .where(Trip.vehicle_id == vehicle.id, Trip.day >= since, Trip.day < month, Trip.destination != "")
                      .group_by(Trip.destination, Trip.origin, Trip.category).order_by(func.count().desc()).limit(15)).all()
    return [dict(ciel=d, odkial=o, typ=c, pocet_jazd=n, km_priemer=history_km(db, vehicle, d, o or None) or 0)
            for d, o, c, n in rows]


def fit_counts(db, vehicle, session, state):
    """For destinations the assistant proposed itself: change how many times each is driven so that the sum of their
    usual lengths is close to the kilometres left to plan. Returns True when counts changed."""
    left = km_to_plan(db, vehicle, session, state)
    routes = [r for r in state["routes"] if r.get("approximate_km")]
    if not left or left <= 0 or not routes:
        return False
    before = [r["count"] for r in routes]
    for _ in range(60):
        gap = sum(r["count"] * r["approximate_km"] for r in routes) - left
        if abs(gap) <= left * 0.12:
            break
        if gap > 0:  # too many kilometres: drop the occurrence that brings the sum closest, keep at least one route
            driven = [r for r in routes if r["count"] > 0]
            options = [r for r in driven if r["count"] > 1] or (driven if len(driven) > 1 else [])
            pick = min(options, key=lambda r: abs(gap - r["approximate_km"]), default=None)
            if pick is None or abs(gap - pick["approximate_km"]) >= abs(gap):
                break
            pick["count"] -= 1
        else:  # too few: add the occurrence that brings the sum closest
            pick = min(routes, key=lambda r: abs(gap + r["approximate_km"]))
            if abs(gap + pick["approximate_km"]) >= abs(gap) or sum(r["count"] for r in routes) >= 40:
                break
            pick["count"] += 1
    state["routes"] = [r for r in state["routes"] if r["count"] > 0]
    return before != [r["count"] for r in routes]


def routes_from_history(db, vehicle, session, state):
    """Deterministic month description when the user asks to invent trips and the model returned none: the vehicle's
    frequent destinations (else its catalogue), driven often enough to cover the kilometres left to plan and to have
    a trip between every two refuels without an odometer."""
    left = km_to_plan(db, vehicle, session, state)
    if not left or left <= 0:
        return []
    options = [(h["ciel"], h["odkial"], h["typ"], h["km_priemer"]) for h in destination_history(db, vehicle, session.month)
               if h["km_priemer"] > 0][:8]
    if not options:
        options = [(d.name, d.origin, "mesto" if distances.is_city(d.name) else "mimo mesto", d.distance * 2)
                   for d in catalogue(db, vehicle) if d.distance][:8]
    if not options:
        return []
    minimum = min(12, sum(1 for row in refuels(db, vehicle, session, state) if row[1] is None) + 1)
    counts, total, index = [0] * len(options), 0, 0
    while (total < left * 0.9 or sum(counts) < minimum) and sum(counts) < 40:
        counts[index % len(options)] += 1
        total += options[index % len(options)][3]
        index += 1
    routes = [dict(destination=name[:150], origin=(origin or "")[:150], count=count, approximate_km=length, category=category,
                   purpose="", days=[]) for (name, origin, category, length), count in zip(options, counts) if count]
    state["routes"] = routes
    fit_counts(db, vehicle, session, state)
    return state["routes"]


def fuel_context(db, vehicle, session, state):
    tank = tank_balance(db, vehicle, session, state)
    available = D(tank["opening"]) + D(tank["purchased"])
    rate = planning_rate(vehicle, state)
    return dict(objem_nadrze_l=tank["capacity"], v_nadrzi_na_zaciatku_l=tank["opening"], natankovane_l=tank["purchased"],
                k_dispozicii_l=available, norma_mesto_l_100km=vehicle.city_rate, norma_mimo_mesta_l_100km=vehicle.outside_rate,
                palivo_vystaci_na_km=int(available * 100 / rate) if rate > 0 else None,
                spotreba_navrhu_l=tank["consumed"] if tank["planned"] else None,
                na_konci_mesiaca_l=tank["closing"] if tank["planned"] else None, upozornenia_nadrze=tank["warnings"])


def context(db, vehicle, session, state):
    return dict(
        mesiac=session.month.isoformat()[:7], vozidlo=f"{vehicle.registration} · {vehicle.name}",
        tachometer=dict(zaciatok=state["start_odo"], koniec=state["end_odo"],
                        datum_odcitania_konca=state.get("end_day") or month_bounds(session)[1].isoformat(),
                        stav_ku_koncu_mesiaca=closing(db, vehicle, session, state)[1],
                        ocakavany_zaciatok_podla_evidencie=expected_start(db, vehicle, session.month)),
        vychodisko=state["origin"],
        nahradit_ulozeny_odhad=state.get("replace_existing", False),
        v_knihe=book_context(db, vehicle, session, state),
        historia_cielov=destination_history(db, vehicle, session.month),
        palivo=fuel_context(db, vehicle, session, state),
        katalog_cielov=[dict(nazov=d.name, odkial=d.origin, km_jedna_cesta=d.distance) for d in catalogue(db, vehicle)][:50],
        trasy=state["routes"],
        blocky=[dict(row=r["id"], poradie=i + 1, datum=r.get("day"), stanica=r.get("station"), litre=r.get("liters"),
                     suma=r.get("gross"), mena=r.get("currency"), tachometer=r.get("odo"),
                     duplicita=r.get("duplicate")) for i, r in enumerate(state["receipts"])],
        jazdy=[dict(row=t["id"], datum=t["day"], ciel=t["destination"], typ=t["category"], km=t.get("distance"),
                    datum_potvrdeny=t.get("day_confirmed"), km_potvrdene=t.get("km_confirmed")) for t in state["trips"]],
        otvorene_problemy=state["problems"])


@router.post("/assistant/{session_id}/operations")
def operations(session_id: str, data: OperationsIn, db=Db, user=Member):
    session, vehicle, state = session_for(db, user, session_id)
    require_draft(session)
    changed = []
    for op in data.operations:
        try:
            changed += apply_operation(db, vehicle, session, state, op)
        except Problem as problem:
            fail(str(problem))
    planned, plan_text = auto_plan(db, vehicle, session, state)
    if planned or (plan_text and any(op.action in {"set_odometers", "set_replace"} for op in data.operations)):
        say(state, "assistant", plan_text, planned)
    changed += planned
    if any(op.action == "set_odometers" and (op.end_day or "end_day" in op.clear) for op in data.operations):
        text = closing_text(db, vehicle, session, state)
        if text:
            say(state, "assistant", text, ["odo"])
    refresh(db, vehicle, session, state)
    if state["moves"]:
        say(state, "assistant", "\n".join(move["text"] for move in state["moves"]), [m["id"] for m in state["moves"]])
        changed += [move["id"] for move in state["moves"]]
    store(session, state, changed)
    db.commit()
    return view(db, vehicle, session)


@router.post("/assistant/{session_id}/plan")
def plan(session_id: str, data: PlanIn, db=Db, user=Member):
    session, vehicle, state = session_for(db, user, session_id)
    require_draft(session)
    try:
        changed = build_balanced(db, vehicle, session, state)[0] if data.rebuild or not state["trips"] else [t["id"] for t in state["trips"]]
    except Problem as problem:
        say(state, "assistant", str(problem))
        store(session, state)
        db.commit()
        return view(db, vehicle, session)
    problems = refresh(db, vehicle, session, state)
    estimated = sum(not t["day_confirmed"] for t in state["trips"])
    text = (f"Návrh obsahuje {len(state['trips'])} {plural(len(state['trips']), 'jazdu', 'jazdy', 'jázd')} ({km(sum(t['distance'] for t in state['trips']))}). "
            f"Počet odhadnutých dátumov: {estimated}; vzdialenosti sú dopočítané z tachometra. "
            "Konkrétne dni alebo kilometre mi môžete opraviť v chate, napríklad „V Poprade som bol 14. októbra“.")
    say(state, "assistant", text + ("\n" + "\n".join(problems) if problems else "") + "\n"
        + tank_text(tank_balance(db, vehicle, session, state)), changed)
    store(session, state, changed)
    db.commit()
    return view(db, vehicle, session)


@router.post("/assistant/{session_id}/approve")
def approve(session_id: str, data: ApproveIn, db=Db, user=Member):
    session, vehicle, state = session_for(db, user, session_id)
    if session.status == "saved":
        return view(db, vehicle, session)  # repeated click returns the stored result
    if data.version != session.version:
        fail("Koncept sa medzitým zmenil. Skontrolujte aktuálny stav a schváľte ho znova.", 409)
    problems = receipt_checks(db, vehicle, session, state)
    if problems:
        fail(problems[0], 409)
    if state["receipts"] and not data.receipts_checked:
        fail("Potvrďte, že ste údaje z bločkov skontrolovali podľa originálov.", 409)
    if not state["trips"]:
        fail("Koncept neobsahuje žiadne jazdy.", 409)
    if state["fingerprint"] != planner.fingerprint(db, vehicle):
        state["stale"] = True
        say(state, "assistant", "Evidencia vozidla sa od prípravy návrhu zmenila (napríklad pribudla jazda alebo tankovanie). "
            "Kliknite na Prepočítať harmonogram a návrh skontrolujte znova.")
        store(session, state)
        db.commit()
        fail("Evidencia vozidla sa od prípravy návrhu zmenila. Prepočítajte harmonogram a skontrolujte ho.", 409)
    check = deepcopy(state)
    if recalculate(db, vehicle, session, check, balance=False) or [(t["day"], t["distance"]) for t in check["trips"]] != \
            [(t["day"], t["distance"]) for t in state["trips"]]:
        fail("Harmonogram už nezodpovedá kontrolám. Prepočítajte ho a skontrolujte.", 409)
    kept = saved_rows(db, vehicle, session, state)[0]
    odo = state["start_odo"]
    for _, start, end in sorted([(t.day.isoformat(), t.start_odo, t.end_odo) for t in kept]
                                + [(t["day"], t["start_odo"], t["end_odo"]) for t in state["trips"]]):
        if start != odo:
            fail("Jazdy nenadväzujú na tachometer. Prepočítajte harmonogram a skontrolujte ho.", 409)
        odo = end
    close_day, close_odo, estimated = closing(db, vehicle, session, state)
    if odo != close_odo:
        fail("Súčet jázd sa nerovná rozdielu tachometra.", 409)
    for receipt in state["receipts"]:
        same = db.scalar(select(Fuel.id).where(Fuel.vehicle_id == vehicle.id, Fuel.day == date.fromisoformat(receipt["day"]),
                         Fuel.liters == D(receipt["liters"]), Fuel.gross == D(receipt["gross"])).limit(1))
        if same and not receipt.get("duplicate_ok"):
            fail(f"Tankovanie z bločku {receipt['id']} už je v evidencii.", 409)
    replaced = []
    if state.get("replace_existing"):
        info = month_trips(db, vehicle, session.month)
        for old in db.scalars(select(Trip).where(Trip.id.in_(info["ids"]))):
            replaced.append(json_value({c.name: getattr(old, c.name) for c in old.__table__.columns}))
            db.delete(old)
        db.flush()
    draft = MonthlyDraft(id=str(uuid4()), vehicle_id=vehicle.id, user_id=user.id, month=session.month,
                         fingerprint=state["fingerprint"], payload=json_value(dict(
                             source="assistant", session_id=session.id, start_odo=state["start_odo"], end_odo=close_odo,
                             reading=dict(day=state.get("end_day"), odo=state["end_odo"], estimated=estimated),
                             routes=state["routes"], rows=state["trips"], receipts=state["receipts"], warnings=[])))
    db.add(draft)
    db.flush()
    fuels, trips = [], []
    for receipt in state["receipts"]:
        note = "Bloček zo sprievodcu" + (f" · {receipt['file_name']}" if receipt.get("file_name") else " · zadané ručne")
        if receipt.get("odo") is None:
            note += " · tachometer na doklade neuvedený"
        fuel = Fuel(vehicle_id=vehicle.id, day=date.fromisoformat(receipt["day"]), odo=receipt.get("odo"),
                    liters=D(receipt["liters"]), gross=D(receipt["gross"]), vat=D(receipt.get("vat") or default_vat(receipt["day"])),
                    station=(receipt.get("station") or "")[:150], clock="", note=note, measured=None, draft_id=draft.id)
        db.add(fuel)
        fuels.append(fuel)
    for trip in state["trips"]:
        flags = ["Odhad z mesačného sprievodcu"]
        flags.append("dátum potvrdený používateľom" if trip["day_confirmed"] else "dátum odhadnutý")
        flags.append("km potvrdené používateľom" if trip["km_confirmed"] else "km dopočítané z tachometra")
        row = Trip(vehicle_id=vehicle.id, day=date.fromisoformat(trip["day"]), start_odo=trip["start_odo"],
                   end_odo=trip["end_odo"], origin=trip["origin"][:150], destination=trip["destination"][:150],
                   purpose=trip["purpose"][:150], category=trip["category"], note=" · ".join(flags),
                   rate=norm(vehicle, trip["category"]),
                   estimated=True, draft_id=draft.id)
        db.add(row)
        trips.append(row)
    db.flush()
    result = dict(draft_id=draft.id, trip_ids=[t.id for t in trips], fuel_ids=[f.id for f in fuels],
                  year=session.month.year, month=session.month.month)
    draft.result = result
    session.result = result
    session.status = "saved"
    say(state, "assistant", f"Výkaz je uložený: {len(trips)} {plural(len(trips), 'jazda', 'jazdy', 'jázd')} a {len(fuels)} "
        f"{plural(len(fuels), 'tankovanie', 'tankovania', 'tankovaní')}"
        + (f", nahradených {len(replaced)} skôr uložených odhadov" if replaced else "") + ". Môžete stiahnuť PDF alebo XLSX.")
    store(session, state, [])
    db.add(Audit(user_id=user.id, vehicle_id=vehicle.id, action="Mesačný sprievodca schválený", data=json_value(dict(
        session_id=session.id, draft_id=draft.id, month=session.month, trips=result["trip_ids"], fuels=result["fuel_ids"],
        replaced_trips=replaced,
        model=gemini.model_name()))))
    db.commit()
    db.info.pop("assistant", None)  # the month's rows changed
    return view(db, vehicle, session)

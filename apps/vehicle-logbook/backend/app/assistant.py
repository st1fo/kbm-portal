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
from .db import Access, AssistantFile, AssistantSession, Audit, Closing, Destination, Fuel, Holiday, MonthlyDraft, Trip, Vehicle, now
from .ledger import ledger, month_of, next_month, rounded

router = APIRouter(prefix="/api")
Db = Depends(db_session)
Member = Depends(current_user)

MAX_FILE_BYTES = 8_000_000
MAX_FILES = 20
MAX_MESSAGE = 2000
MAX_MESSAGES = 300
MAX_TRIPS = 120
MAX_GEMINI_CALLS = 150
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
    return dict(start_odo=None, end_odo=None, end_day=None, closing=None, origin=distances.HOME, routes=[], files=[], receipts=[], moves=[],
                replace_existing=False,
                receipts_confirmed=False, trips=[], messages=[], highlight=[], problems=[],
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


def expected_start(db, vehicle, month):
    previous = db.scalar(select(Trip).where(Trip.vehicle_id == vehicle.id, Trip.day < month)
                         .order_by(Trip.day.desc(), Trip.end_odo.desc()).limit(1))
    return previous.end_odo if previous else vehicle.initial_odo


def catalogue(db, vehicle):
    return list(db.scalars(select(Destination).where(Destination.vehicle_id == vehicle.id).order_by(Destination.name)))


def known_km(db, vehicle, state, destination, origin=None):
    """Round-trip km: vehicle catalogue for this origin, then the Žilina table, then the draft's routes."""
    origin = origin or state.get("origin") or distances.HOME
    name = planner.normalized(destination or "")
    matches = [item for item in catalogue(db, vehicle) if planner.normalized(item.name) == name
               and (not item.origin or planner.normalized(item.origin) == planner.normalized(origin))]
    if matches:
        return matches[0].distance * 2
    builtin = distances.round_trip(destination, origin)
    if builtin:
        return builtin
    for route in state.get("routes", []):
        if planner.normalized(route["destination"]) == name and route.get("approximate_km"):
            return route["approximate_km"]
    return None


def month_trips(db, vehicle, month):
    """Trips already saved in the month: estimates from an earlier monthly draft can be replaced, manual ones stay
    and the draft only fills the kilometres between them."""
    rows = list(db.scalars(select(Trip).where(Trip.vehicle_id == vehicle.id, Trip.day >= month, Trip.day < next_month(month))))
    replaceable = [row for row in rows if row.draft_id]
    manual = [row for row in rows if not row.draft_id]
    return dict(count=len(rows), replaceable=len(replaceable), manual=len(manual),
                km=sum(row.end_odo - row.start_odo for row in replaceable),
                manual_km=sum(row.end_odo - row.start_odo for row in manual), ids=[row.id for row in replaceable])


def saved_rows(db, vehicle, session, state):
    """Fuel purchases and trips of the month already in the logbook (e.g. a FleetCor invoice, trips entered by hand).
    They are read-only in the draft; trips replaced by this draft (earlier estimates) are left out."""
    first, stop = session.month, next_month(session.month)
    replaced = set(month_trips(db, vehicle, first)["ids"]) if state.get("replace_existing") else set()
    trips = [trip for trip in db.scalars(select(Trip).where(Trip.vehicle_id == vehicle.id, Trip.day >= first, Trip.day < stop)
                                         .order_by(Trip.day, Trip.start_odo, Trip.id)) if trip.id not in replaced]
    fuels = list(db.scalars(select(Fuel).where(Fuel.vehicle_id == vehicle.id, Fuel.day >= first, Fuel.day < stop)
                            .order_by(Fuel.day, Fuel.clock, Fuel.id)))
    return trips, fuels


def existing_text(info, month, manual=True):
    name = f"{month.month}/{month.year}"
    parts = []
    if manual and info["manual"]:
        parts.append(f"V mesiaci {name} je v knihe jázd už {info['manual']} ručne zapísaných jázd ({km(info['manual_km'])}). "
                     "Ponechám ich bez zmeny a navrhnem iba jazdy pre zvyšné kilometre.")
    if info["replaceable"]:
        parts.append(f"Mesiac {name} už obsahuje {info['replaceable']} jázd ({km(info['km'])}) uložených skôr ako mesačný "
                     "odhad. Ak ich chcete nahradiť týmto návrhom, zapnite vľavo voľbu „Nahradiť uložený odhad“. Staré "
                     "jazdy sa odstránia až pri schválení výkazu, v tej istej transakcii.")
    return " ".join(parts)


def saved_text(db, vehicle, session, state):
    """Greeting line about rows loaded from the logbook."""
    trips, fuels = saved_rows(db, vehicle, session, state)
    parts = []
    if fuels:
        parts.append(f"{len(fuels)} {'tankovanie' if len(fuels) == 1 else 'tankovania' if len(fuels) < 5 else 'tankovaní'} "
                     f"({liters(sum((f.liters for f in fuels), D(0)))})")
    manual = [trip for trip in trips if not trip.draft_id]
    if manual:
        parts.append(f"{len(manual)} {'jazdu' if len(manual) == 1 else 'jazdy' if len(manual) < 5 else 'jázd'} "
                     f"({km(sum(t.end_odo - t.start_odo for t in manual))})")
    if not parts:
        return ""
    text = f"Z knihy jázd som načítal {' a '.join(parts)} z tohto mesiaca. Vidíte ich v tabuľkách so štítkom „v knihe“ a započítam ich."
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
    """Mark missing fields and possible duplicates; returns blocking problems for confirmation."""
    first, last = month_bounds(session)
    existing = list(db.scalars(select(Fuel).where(Fuel.vehicle_id == vehicle.id, Fuel.day >= first, Fuel.day <= last)))
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


def unconfirm(state, receipt):
    receipt["confirmed"] = False
    state["receipts_confirmed"] = False


def extract_pending(db, session, state):
    """Send unprocessed uploads to Gemini. Each processed file is committed so failures keep the draft."""
    notes, changed = [], []
    for item in state["files"]:
        if item["status"] not in {"pending", "error"}:
            continue
        stored = db.get(AssistantFile, item["id"])
        if not stored:
            item.update(status="error", error="Súbor sa nenašiel.")
            continue
        if state["gemini_calls"] >= MAX_GEMINI_CALLS:
            raise gemini.AssistantError("Koncept dosiahol limit volaní Gemini. Zvyšné údaje doplňte ručne.")
        state["gemini_calls"] += 1
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
                           confirmed=False, duplicate_ok=False, unreadable=[])
            receipt.update(clean_receipt(fields))
            # Only fields that are both reported illegible and actually empty need the user's attention.
            receipt["unreadable"] = sorted({name for name in fields.unreadable
                                            if name in {"day", "station", "liters", "gross"} and not receipt.get(name)})
            state["receipts"].append(receipt)
            changed.append(receipt["id"])
        item.update(status="done", error=None)
        state["receipts_confirmed"] = False
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

def anchors(db, vehicle, session, state):
    """Odometer readings the schedule must hit: saved fuel receipts and confirmed draft receipts."""
    first, last = month_bounds(session)
    points = {(fuel.day, fuel.odo) for fuel in db.scalars(select(Fuel).where(
        Fuel.vehicle_id == vehicle.id, Fuel.day >= first, Fuel.day <= last, Fuel.odo.is_not(None)))}
    for receipt in state["receipts"]:
        if receipt.get("odo") is not None and receipt.get("day"):
            points.add((date.fromisoformat(receipt["day"]), receipt["odo"]))
    for day, start, end in kept_spans(db, vehicle, session, state):
        points |= {(day, start), (day, end)}
    return sorted(points)


def working_days(db, vehicle, start, stop):
    """Number of weekdays without a vehicle holiday in start..stop (inclusive)."""
    if stop < start:
        return 0
    holidays = set(db.scalars(select(Holiday.day).where(Holiday.vehicle_id == vehicle.id, Holiday.day >= start, Holiday.day <= stop)))
    return sum(1 for i in range((stop - start).days + 1)
               if (day := start + timedelta(days=i)).weekday() < 5 and day not in holidays)


def closing(db, vehicle, session, state, points=None):
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
    points = anchors(db, vehicle, session, state) if points is None else points
    prior_day, prior_odo = points[-1] if points else (max(first, vehicle.start_date) - timedelta(days=1), state["start_odo"])
    if prior_odo is None or state["end_odo"] <= prior_odo:
        return last, state["end_odo"], False
    inside = working_days(db, vehicle, prior_day + timedelta(days=1), last)
    total = working_days(db, vehicle, prior_day + timedelta(days=1), read)
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


def kept_spans(db, vehicle, session, state):
    """(day, start odo, end odo) of trips that stay in the logbook; the draft plans only the kilometres between them."""
    return [(trip.day, trip.start_odo, trip.end_odo) for trip in saved_rows(db, vehicle, session, state)[0]]


def tank_opening(db, vehicle, month):
    history = [row for row in ledger(db, vehicle) if row["day"] < month.isoformat()]
    return D(history[-1]["end_liters"]) if history else D(vehicle.initial_liters)


def planning_rate(vehicle, state):
    """Norm consumption (l/100 km) of the planned trips, weighted by their approximate length."""
    rows = state["trips"] or state["routes"]
    weights = [(D(r.get("approximate_km") or r.get("distance") or 1) * D(r.get("count", 1)), r.get("category")) for r in rows]
    total = sum((w for w, _ in weights), D(0))
    if not total:
        return D(vehicle.outside_rate)
    return sum((w * D(vehicle.city_rate if c == "mesto" else vehicle.outside_rate) for w, c in weights), D(0)) / total


def soft_refuels(db, vehicle, session, state):
    """Refuels without an odometer reading (FleetCor invoices, most receipts) as (day, liters, measured), by day."""
    first, last = month_bounds(session)
    rows = [(f.day, D(f.liters), f.measured) for f in db.scalars(select(Fuel).where(
        Fuel.vehicle_id == vehicle.id, Fuel.day >= first, Fuel.day <= last, Fuel.odo.is_(None)))]
    rows += [(date.fromisoformat(r["day"]), D(r["liters"]), None) for r in state["receipts"]
             if r.get("day") and r.get("liters") and r.get("odo") is None]
    return sorted(rows, key=lambda row: row[0])


def hard_refuels(db, vehicle, session, state):
    """Refuels with an odometer reading as (day, odo, liters, measured)."""
    first, last = month_bounds(session)
    rows = [(f.day, f.odo, D(f.liters), f.measured) for f in db.scalars(select(Fuel).where(
        Fuel.vehicle_id == vehicle.id, Fuel.day >= first, Fuel.day <= last, Fuel.odo.is_not(None)))]
    rows += [(date.fromisoformat(r["day"]), r["odo"], D(r["liters"]), None) for r in state["receipts"]
             if r.get("day") and r.get("liters") and r.get("odo") is not None]
    return rows


def best_subset(amounts, low, high, target):
    """Indexes of `amounts` whose sum lies in [low, high], as close to `target` as possible (subset sum in 0.1 l).
    If no subset fits, the closest one, preferring to stay below `high` (a dry tank is worse than a too full one)."""
    unit = lambda value: int((D(value) * 10).to_integral_value())
    low, high, target = unit(low), unit(high), unit(target)
    limit = high + max([unit(a) for a in amounts] or [0])
    sums = {0: ()}
    for index, amount in enumerate(amounts):
        step = unit(amount)
        for total, picked in list(sums.items()):
            if total + step <= limit and total + step not in sums:
                sums[total + step] = picked + (index,)
    inside = [total for total in sums if low <= total <= high]
    if inside:
        return list(sums[min(inside, key=lambda total: (abs(total - target), total))])
    return list(sums[min(sums, key=lambda total: (max(low - total, total - high), total > high))])


def spread(trips, days):
    """Give trips distinct, evenly spread days (keeps their order)."""
    for position, trip in enumerate(trips):
        trip["day"] = days[min(len(days) - 1, ((position + 1) * len(days) - 1) // len(trips))].isoformat()


def schedule_for_fuel(db, vehicle, session, state, segments):
    """Choose the days of estimated-date trips so that, by the norm, the tank never runs dry before a refuel and
    every refuel fits into the tank. Kilometres per trip stay as planned (they follow the destinations); only the
    order around refuels without an odometer changes. Returns the ids of trips whose day changed."""
    capacity = D(vehicle.capacity)
    rate = lambda trip: D(vehicle.city_rate if trip["category"] == "mesto" else vehicle.outside_rate)
    use = lambda trip: D(trip.get("distance") or 0) * rate(trip) / 100
    level = tank_opening(db, vehicle, session.month)
    refuels, hard = soft_refuels(db, vehicle, session, state), hard_refuels(db, vehicle, session, state)
    need = sum((use(t) for t in state["trips"]), D(0)) + sum(
        (D(s["budget"]) * D(vehicle.outside_rate) / 100 for s in segments if s["fixed"]), D(0))
    short = level + sum((r[1] for r in refuels), D(0)) + sum((h[2] for h in hard), D(0)) < need
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
        for number, (day, liters, measured) in enumerate(refuels):
            if number in used_soft or not start <= day <= segment["last"]:
                continue
            used_soft.add(number)
            available = level - sum((use(t) for t in dated if previous.isoformat() < t["day"] <= day.isoformat()), D(0))
            # a slightly too full tank (within the ledger's tolerance) is fine, a dry one is not
            low = max(D(0), available + liters - capacity - max(D(1), capacity * D("0.03")))
            high = max(D(0), available)
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
            first_day = min(previous + timedelta(days=1), segment["last"])
            spread(pool, workdays(db, vehicle, first_day, segment["last"]))
        level -= sum((use(t) for t in pool), D(0)) + sum((use(t) for t in dated if t["day"] > previous.isoformat()), D(0))
    sort_trips(state)
    return [t["id"] for t in state["trips"] if before.get(t["id"]) != t["day"]]


def check_month(db, vehicle, session, state):
    """Backend rules for the month: closing, existing trips, odometer continuity and receipt readings."""
    if state["start_odo"] is None or state["end_odo"] is None:
        missing = [label for key, label in (("start_odo", "na začiatku"), ("end_odo", "na konci"))
                   if state[key] is None]
        raise Problem(f"Aký bol stav tachometra {' a '.join(missing)} mesiaca?")
    if state["end_odo"] <= state["start_odo"]:
        raise Problem("Koncový stav tachometra musí byť vyšší ako počiatočný. Skontrolujte, prosím, obe hodnoty.")
    close_day, close_odo, _ = closing(db, vehicle, session, state)
    state["closing"] = [close_day.isoformat(), close_odo]
    spans = kept_spans(db, vehicle, session, state)
    extra = [(date.fromisoformat(r["day"]), r["odo"]) for r in state["receipts"]
             if r.get("odo") is not None and r.get("day")]
    extra += [(day, odo) for day, start, end in spans for odo in (start, end)]
    try:
        planner.month_context(db, vehicle, session.month, state["start_odo"], close_odo, today(), extra,
                              replaceable=state.get("replace_existing", False), keep_manual=True)
    except planner.PlanError as error:
        message = str(error)
        if "Mesiac už obsahuje jazdy" in message:
            message = existing_text(month_trips(db, vehicle, session.month), session.month, manual=False)
        if "Tachometre tankovaní" in message:
            details = ", ".join(f"{sk_day(day)} {km(odo)}" for day, odo in anchors(db, vehicle, session, state))
            source = "na bločkoch a v uložených jazdách" if spans else "na bločkoch"
            message = (f"Stavy tachometra {source} ({details}) nesedia s rozsahom {km(state['start_odo'])} – "
                       f"{km(state['end_odo'])} alebo nejdú chronologicky. Ktorá hodnota je správna?")
        raise Problem(message) from error
    return anchors(db, vehicle, session, state), spans


def segments_for(session, state, points, vehicle=None, spans=()):
    """Odometer segments between anchors. A segment covered by a trip kept in the logbook is `fixed`: draft trips
    never go there and its kilometres are already accounted for."""
    first, last = month_bounds(session)
    if vehicle is not None:
        first = max(first, vehicle.start_date)
    result, prior_day, prior_odo = [], first, state["start_odo"]
    close = state.get("closing") or [last.isoformat(), state["end_odo"]]
    for day, odo in [*points, (date.fromisoformat(close[0]), close[1])]:
        fixed = odo > prior_odo and prior_day == day and any(
            span_day == day and start <= prior_odo and odo <= end for span_day, start, end in spans)
        result.append(dict(first=prior_day, last=day, start=prior_odo, end=odo, budget=odo - prior_odo, trips=[], fixed=fixed))
        prior_day, prior_odo = day, odo
    return result


def workdays(db, vehicle, start, stop):
    holidays = set(db.scalars(select(Holiday.day).where(Holiday.vehicle_id == vehicle.id)))
    days = [start + timedelta(days=i) for i in range((stop - start).days + 1)]
    return [day for day in days if day.weekday() < 5 and day not in holidays] or days


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


def segment_days(db, vehicle, segments, index):
    segment = segments[index]
    start = segment["first"] + timedelta(days=1) if index else segment["first"]
    return workdays(db, vehicle, min(start, segment["last"]), segment["last"])


def fill_gaps(db, vehicle, session, state, points, spans=()):
    """Move estimated-date trips into odometer segments left without a trip (e.g. after a user fixed a date)."""
    moves = []
    for _ in range(len(state["trips"])):
        segments = segments_for(session, state, points, vehicle, spans)
        assign(segments, state["trips"])
        empty = [i for i, s in enumerate(segments) if s["budget"] > 0 and not s["trips"] and not s["fixed"]]
        donors = [(abs(j - empty[0]), j) for j, s in enumerate(segments) if empty and len(s["trips"]) > 1
                  and any(not t["day_confirmed"] for t in s["trips"])]
        if not empty or not donors:
            return segments, moves
        target, donor = empty[0], min(donors)[1]
        movable = [t for t in segments[donor]["trips"] if not t["day_confirmed"]]
        trip = movable[0] if donor > target else movable[-1]
        days = segment_days(db, vehicle, segments, target)
        trip["day"] = days[len(days) // 2].isoformat()
        sort_trips(state)
        moves.append(dict(id=trip["id"], text=f"Jazdu {trip['id']} ({trip['destination']}) som presunul na {sk_day(trip['day'])}, "
                          f"aby tachometer sedel s tankovaním {sk_day(segments[target]['last'])} ({km(segments[target]['end'])})."))
    return segments_for(session, state, points, vehicle, spans), moves


def pull_before_reading(db, vehicle, session, state, points, spans):
    """Estimated-date trips after the closing reading day move into the last days before it."""
    close = date.fromisoformat(state["closing"][0])
    late = [t for t in state["trips"] if t["day"] > close.isoformat() and not t["day_confirmed"]]
    if not late:
        return []
    tail = segments_for(session, state, points, vehicle, spans)[-1]
    days = workdays(db, vehicle, min(tail["first"] + timedelta(days=1), close), close)
    for position, trip in enumerate(late):
        trip["day"] = days[min(len(days) - 1, ((position + 1) * len(days) - 1) // len(late))].isoformat()
    sort_trips(state)
    return [dict(id=t["id"], text=f"Jazdu {t['id']} ({t['destination']}) som presunul na {sk_day(t['day'])}, "
                 f"pred odčítanie tachometra {sk_day(close)}.") for t in late]


def recalculate(db, vehicle, session, state, balance=True):
    """Re-run all backend checks on the draft schedule. Returns problems the assistant asks about. With `balance`, the
    days of estimated trips are re-chosen when a fresh plan asks for it or the tank would run dry / overflow."""
    sort_trips(state)
    state["moves"] = []
    if not state["trips"]:
        return []
    placed = None
    try:
        points, spans = check_month(db, vehicle, session, state)
        state["moves"] = pull_before_reading(db, vehicle, session, state, points, spans)
        segments, moves = fill_gaps(db, vehicle, session, state, points, spans)
        state["moves"] += moves
        segments = segments_for(session, state, points, vehicle, spans)
        early = [t["id"] for t in state["trips"] if t["day"] < vehicle.start_date.isoformat()]
        if early:
            raise Problem(f"Jazdy {', '.join(early)} sú pred začiatkom evidencie vozidla ({sk_day(vehicle.start_date)}). Opravte ich dátum.")
        assign(segments, state["trips"])
        problems = distribute(segments)
        placed = segments
        fresh = state.pop("schedule_fuel", False)
        if balance and not problems:
            chain(state, placed)
            if fresh or tank_balance(db, vehicle, session, state)["violations"]:
                moved = schedule_for_fuel(db, vehicle, session, state, segments)
                segments = segments_for(session, state, points, vehicle, spans)
                assign(segments, state["trips"])
                problems = distribute(segments)
                placed = segments
                if moved and not fresh:
                    state["moves"].append(dict(id=moved[0], text="Dni jázd " + ", ".join(moved) + " som posunul podľa "
                                               "tankovaní, aby nádrž nešla do mínusu a tankovanie sa zmestilo do nádrže."))
    except Problem as problem:
        problems = [str(problem)]
    chain(state, placed)
    for trip in state["trips"]:
        ratio = D(trip["distance"]) / trip["approximate_km"] if trip.get("approximate_km") and trip.get("distance") else D(1)
        trip["warning"] = (f"Vzdialenosť sa líši od odhadu {km(trip['approximate_km'])} o viac ako 30 %."
                           if abs(ratio - 1) > D("0.3") and not trip.get("km_confirmed") else None)
    if not problems:
        state["fingerprint"] = planner.fingerprint(db, vehicle)
        state["stale"] = False
    return problems


def build_trips(db, vehicle, session, state):
    """Initial schedule from the routes Gemini understood: user-stated dates stay, the rest is spread."""
    if not state["routes"]:
        raise Problem("Kde ste počas mesiaca približne jazdili? Napíšte napríklad „trikrát Košice, dvakrát Poprad“.")
    points, spans = check_month(db, vehicle, session, state)
    first, last = month_bounds(session)
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

    # Around refuels without an odometer the schedule only chooses days: there must be at least one trip more than
    # such refuels, so every stretch between them can get one. Trip lengths stay as the destinations dictate.
    added = 0
    soft = [r for r in soft_refuels(db, vehicle, session, state) if first <= r[0] <= last]
    by_length = sorted(state["routes"], key=lambda r: -(r.get("approximate_km") or 0))
    while soft and free and len(fixed) + len(free) <= len(soft) and len(fixed) + len(free) < MAX_TRIPS:
        route = by_length[added % len(by_length)]
        route["count"] += 1
        free.append(route)
        added += 1

    def make(route, day, confirmed):
        return dict(id="", day=day, origin=route.get("origin") or state["origin"] or distances.HOME, destination=route["destination"],
                    category=route["category"], purpose=route.get("purpose") or "",
                    approximate_km=known_km(db, vehicle, dict(state, routes=[]), route["destination"], route.get("origin") or None)
                    or route.get("approximate_km"),
                    distance=0, day_confirmed=confirmed, km_confirmed=False, order=0)
    segments = segments_for(session, state, points, vehicle, spans)
    trips = sorted((make(route, day, True) for route, day in fixed), key=lambda t: t["day"])
    assign(segments, trips)
    counts = [len(s["trips"]) for s in segments]
    open_segments = [i for i, s in enumerate(segments) if s["budget"] > 0 and not s["fixed"]]
    empty = [i for i in open_segments if not counts[i]]
    if not open_segments:
        raise Problem("Všetky kilometre mesiaca už pokrývajú jazdy uložené v knihe. Nie je čo navrhnúť.")
    # Every stretch between refuels/readings needs a trip: repeat the longest destinations rather than let a refuel
    # overflow the tank or the tank run dry.
    while len(empty) > len(free) and len(fixed) + len(free) < MAX_TRIPS:
        route = by_length[added % len(by_length)]
        route["count"] += 1
        free.append(route)
        added += 1
    state["added_for_fuel"] = added
    if len(empty) > len(free):
        source = "Tankovania a uložené jazdy" if spans else "Tankovania"
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
        dates = segment_days(db, vehicle, segments, index)
        for position, occurrence in enumerate(sorted(picked)):
            day = dates[min(len(dates) - 1, ((position + 1) * len(dates) - 1) // len(picked))]
            trips.append(make(free[occurrence], day.isoformat(), False))
    trips.sort(key=lambda t: (t["day"], not t["day_confirmed"]))
    state["counter"]["J"] = 0
    for trip in trips:
        trip["id"] = next_id(state, "J")
    state["trips"] = trips
    state["schedule_fuel"] = True  # days are re-chosen around refuels once kilometres are known (recalculate)
    return [trip["id"] for trip in trips]


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
        removed = [r["id"] for r in state["receipts"] if r.get("file_id") == item["id"]]
        state["receipts"] = [r for r in state["receipts"] if r.get("file_id") != item["id"]]
        remove_file(db, state, item["id"])
        if removed:
            state["receipts_confirmed"] = False
        return []
    if op.action == "add_receipt":
        if len(state["receipts"]) >= 60:
            raise Problem("Koncept môže mať najviac 60 bločkov.")
        receipt = dict(id=next_id(state, "B"), file_id=None, file_name=None, source="ručne", confirmed=False,
                       duplicate_ok=False, unreadable=[], day=op.day.isoformat() if op.day else None,
                       station=op.station or "", liters=decimal_text(op.liters, "0.001"), gross=decimal_text(op.gross, "0.01"),
                       currency=op.currency or "EUR", vat=decimal_text(op.vat, "0.01") if op.vat is not None else default_vat(op.day and op.day.isoformat()),
                       odo=op.odo)
        state["receipts"].append(receipt)
        state["receipts_confirmed"] = False
        return [receipt["id"]]
    receipt = find(state, "receipts", op.row)
    if op.action == "delete_receipt":
        state["receipts"].remove(receipt)
        if receipt.get("file_id") and not any(r.get("file_id") == receipt["file_id"] for r in state["receipts"]):
            remove_file(db, state, receipt["file_id"])
        state["receipts_confirmed"] = False
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
        unconfirm(state, receipt)
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
    month, stop = session.month, next_month(session.month)
    history = [row for row in ledger(db, vehicle) if row["day"] < month.isoformat()]
    opening = history[-1]["end_liters"] if history else vehicle.initial_liters
    replaced = set(month_trips(db, vehicle, month)["ids"]) if state.get("replace_existing") else set()
    events = []
    # rows saved in the logbook get keys "kniha-j<id>" / "kniha-t<id>" so the draft can show them read-only
    for trip in db.scalars(select(Trip).where(Trip.vehicle_id == vehicle.id, Trip.day >= month, Trip.day < stop)):
        if trip.id not in replaced:
            events.append(((trip.day.isoformat(), trip.end_odo, 0, "", trip.id), "trip", f"kniha-j{trip.id}",
                           trip.rate * (trip.end_odo - trip.start_odo) / 100))
    for fuel in db.scalars(select(Fuel).where(Fuel.vehicle_id == vehicle.id, Fuel.day >= month, Fuel.day < stop)):
        events.append(((fuel.day.isoformat(), fuel.odo if fuel.odo is not None else 10**12, 1, fuel.clock, fuel.id),
                       "fuel", f"kniha-t{fuel.id}", fuel.liters, fuel.measured))
    for index, trip in enumerate(state["trips"]):
        rate = vehicle.city_rate if trip["category"] == "mesto" else vehicle.outside_rate
        events.append(((trip["day"], trip.get("end_odo") or 0, 0, "", index), "trip", trip["id"],
                       rate * D(trip.get("distance") or 0) / 100))
    for index, receipt in enumerate(state["receipts"]):
        if receipt.get("day") and receipt.get("liters"):
            odo = receipt["odo"] if receipt.get("odo") is not None else 10**12
            events.append(((receipt["day"], odo, 1, "", 10**6 + index), "fuel", receipt["id"], D(receipt["liters"]), None))
    level, purchased, consumed, rows, warnings, days = D(opening), D(0), D(0), {}, [], {}
    violations = 0
    tolerance = max(D(1), vehicle.capacity * D("0.03"))
    planned = bool(state["trips"])
    for key, kind, row_id, amount, *rest in sorted(events, key=lambda event: event[0]):
        before = level
        label = f" ({row_id})" if not row_id.startswith("kniha-") else " (v knihe)"
        if kind == "trip":
            level -= amount
            consumed += amount
        else:
            if before < -tolerance:
                violations += 1
            if planned and before < -tolerance:
                warnings.append(f"Pred tankovaním {sk_day(key[0])}{label} by nádrž podľa "
                                f"normy klesla na {liters(before)}. Chýba skoršie tankovanie alebo sú kilometre privysoké.")
            level += amount
            purchased += amount
            if level > vehicle.capacity + tolerance:
                violations += 1
            if planned and level > vehicle.capacity + tolerance:
                warnings.append(f"Tankovanie {sk_day(key[0])}{label}: pred tankovaním "
                                f"{liters(before)} + {liters(amount)} = {liters(level)}, čo je viac ako nádrž "
                                f"{liters(vehicle.capacity)}. Skontrolujte litre na bločku alebo doplňte jazdy pred týmto tankovaním.")
            if rest and rest[0] is not None:
                level = D(rest[0])  # measured tank level after refuel, as in the ledger
        rows[row_id] = dict(before=rounded(before, "0.1"), after=rounded(level, "0.1"))
        days[key[0]] = rounded(level, "0.1")
    if planned and level < -tolerance:
        available = D(opening) + purchased
        rate = planning_rate(vehicle, state)
        month_km = sum((D(t.get("distance") or 0) for t in state["trips"]), D(0)) + sum(
            (D(t.end_odo - t.start_odo) for t in saved_rows(db, vehicle, session, state)[0]), D(0))
        warnings.append(
            f"Palivo na celý mesiac nestačí: na {km(int(month_km))} treba podľa normy {liters(consumed)}, k dispozícii bolo "
            f"{liters(opening)} v nádrži + {liters(purchased)} natankovaných = {liters(available)}. Na konci mesiaca by "
            f"nádrž bola na {liters(level)}. Pri norme {str(rounded(rate, '0.1')).replace('.', ',')} l/100 km vystačí na "
            f"asi {km(int(available * 100 / rate))}. Skontrolujte, či nechýba bloček z tankovania, či sedí koncový stav "
            "tachometra a norma spotreby vozidla. Počas mesiaca jazdy rozdeľujem tak, aby nádrž pred tankovaním do mínusu nešla."
            if rate > 0 else f"Na konci mesiaca by nádrž podľa normy bola na {liters(level)}. Chýba tankovanie alebo sú kilometre privysoké.")
    return dict(opening=rounded(D(opening), "0.1"), purchased=rounded(purchased, "0.1"), consumed=rounded(consumed, "0.1"),
                closing=rounded(level, "0.1"), capacity=rounded(vehicle.capacity, "0.1"), planned=planned,
                rows=rows, days=days, warnings=warnings, violations=violations)


def tank_text(db, vehicle, session, state):
    tank = tank_balance(db, vehicle, session, state)
    text = (f"Nádrž: začiatok {liters(tank['opening'])}, natankované +{liters(tank['purchased'])}"
            + (f", spotreba podľa normy −{liters(tank['consumed'])}, koniec {liters(tank['closing'])}." if tank["planned"] else "."))
    return " ".join([text, *tank["warnings"]])


def view(db, vehicle, session, state=None, error=None):
    state = {**blank_state(), **deepcopy(state or session.state)}
    problems = receipt_checks(db, vehicle, session, state) if session.status == "draft" else []
    kept, fuels = saved_rows(db, vehicle, session, state) if session.status == "draft" else ([], [])
    kept_km = sum(t.end_odo - t.start_odo for t in kept)
    total = sum(t.get("distance") or 0 for t in state["trips"]) + kept_km
    close_day, close_odo, estimated = closing(db, vehicle, session, state)
    expected = (close_odo - state["start_odo"]) if state["start_odo"] is not None and close_odo is not None else None
    can_confirm = session.status == "draft" and not problems and not state["receipts_confirmed"]
    can_plan = session.status == "draft"
    can_approve = (session.status == "draft" and not problems and bool(state["trips"])
                   and not state["problems"] and not state["stale"] and expected == total)
    first, last = month_bounds(session)
    holidays = set(db.scalars(select(Holiday.day).where(Holiday.vehicle_id == vehicle.id, Holiday.day >= first, Holiday.day <= last)))
    month_days = [dict(day=day, holiday=day in holidays, weekend=day.weekday() >= 5,
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
        receipts_confirmed=state["receipts_confirmed"], receipt_problems=problems, trips=state["trips"],
        problems=state["problems"], stale=state["stale"], messages=state["messages"], highlight=state["highlight"],
        existing={k: v for k, v in month_trips(db, vehicle, session.month).items() if k != "ids"},
        tank=tank_balance(db, vehicle, session, state) if session.status == "draft" else None,
        saved=dict(km=kept_km, trips=[dict(key=f"kniha-j{t.id}", day=t.day, origin=t.origin, destination=t.destination,
                                         purpose=t.purpose, category=t.category, distance=t.end_odo - t.start_odo,
                                         start_odo=t.start_odo, end_odo=t.end_odo, estimated=bool(t.draft_id)) for t in kept],
                   fuels=[dict(key=f"kniha-t{f.id}", day=f.day, station=f.station, liters=f.liters, gross=f.gross, odo=f.odo,
                               note=f.note) for f in fuels]),
        replace_existing=state.get("replace_existing", False),
        distance=expected, planned=total, can_confirm=can_confirm, can_plan=can_plan, can_approve=can_approve,
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
    reduce the problems (e.g. they come from fixed odometer readings) is undone."""
    changed = build_trips(db, vehicle, session, state)
    added = state.pop("added_for_fuel", 0)
    for _ in range(8):
        if recalculate(db, vehicle, session, state):
            break
        violations = tank_balance(db, vehicle, session, state)["violations"]
        if not violations:
            break
        movable = [t for t in state["trips"] if not t["day_confirmed"] and not t["km_confirmed"]]
        route = next((r for t in sorted(movable, key=lambda t: -t["distance"]) for r in state["routes"]
                      if r["destination"] == t["destination"]), None)
        if route is None:
            break
        saved, saved_changed = deepcopy(state), changed
        route["count"] += 1
        changed = build_trips(db, vehicle, session, state)
        state.pop("added_for_fuel", None)
        if recalculate(db, vehicle, session, state) or tank_balance(db, vehicle, session, state)["violations"] >= violations:
            state.clear()
            state.update(saved)
            changed = saved_changed
            break
        added += 1
    state["added_for_fuel"] = added
    return changed


def auto_plan(db, vehicle, session, state):
    """Create the first schedule as soon as the month is described (start, end and routes). Returns (ids, text)."""
    if state["trips"] or not state["routes"] or state["start_odo"] is None or state["end_odo"] is None:
        return [], ""
    try:
        changed = build_balanced(db, vehicle, session, state)
    except Problem as problem:
        return [], str(problem)
    added = state.pop("added_for_fuel", 0)
    return changed, (f"Pripravil som návrh {len(state['trips'])} jázd na celý mesiac. Odhady sú označené; "
                     "konkrétne dni alebo kilometre mi môžete opraviť v chate alebo kliknutím na jazdu."
                     + (f" Aby pred každým tankovaním bola jazda a palivo sa zmestilo do nádrže, pridal som {added} "
                        f"{'jazdu' if added == 1 else 'jazdy' if added < 5 else 'jázd'} k najdlhším cieľom." if added else ""))


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
            say(state, "assistant", existing_text(info, month, manual=False))
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
        notes, changed = extract_pending(db, session, state)
        planned, plan_text = auto_plan(db, vehicle, session, state)
        refresh(db, vehicle, session, state)
        if plan_text:
            notes.append(plan_text)
        changed += planned
        if changed:
            problems = receipt_checks(db, vehicle, session, state)
            count = len([c for c in changed if c.startswith("B")])
            text = f"Spracoval som {count} {'bloček' if count == 1 else 'bločky' if count < 5 else 'bločkov'}. " \
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
        receipt_checks(db, vehicle, session, state)
        history = [m for m in state["messages"][:-1] if m["role"] in {"user", "assistant"}][-HISTORY:]
        result = gemini.interpret(context(db, vehicle, session, state), history, data.text)
        reply = honest_reply(result.reply)
        answer = [reply] if reply else []
        answer += notes
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
        for proposal in result.operations[:20]:
            try:
                changed += apply_operation(db, vehicle, session, state, wire_operation(proposal))
            except (Problem, ValueError) as error:
                answer.append(f"Zmenu {proposal.row or ''} som neurobil: {problem_text(error)}")
        # The chat only creates the first schedule; it never silently replaces one the user has edited
        # (a fresh schedule is the explicit "Navrhnúť znova" button). Existing rows are re-checked below.
        if result.create_plan and state["trips"] and state["routes"] and not any(c[:1] in {"B", "J"} for c in changed):
            state["trips"] = []  # the user explicitly asked for a new plan (new or the same description)
        if result.create_plan and not state["routes"] and not state["trips"]:
            state["routes"] = routes_from_history(db, vehicle, session, state)
            if state["routes"]:
                changed.append("routes")
                answer = [a for a in answer if a != (result.reply or "").strip()]  # its text named destinations it did not return
                answer.append("Ciele som vybral z častých jázd tohto vozidla: " + ", ".join(
                    f"{r['destination']} {r['count']}×" for r in state["routes"]) + ". Ide o odhad, môžete ho upraviť.")
        planned, plan_text = auto_plan(db, vehicle, session, state)
        changed += planned
        answer.append(plan_text)
        if result.create_plan and not state["trips"] and not plan_text:
            missing = []
            if state["start_odo"] is None or state["end_odo"] is None:
                missing.append("stav tachometra na začiatku a na konci mesiaca")
            if not state["routes"]:
                missing.append("aspoň približne kam ste jazdili (alebo napíšte „navrhni ciele sám“)")
            if missing:
                answer.append("Návrh jázd zatiaľ nie je vytvorený. Potrebujem ešte: " + " a ".join(missing) + ".")
        problems = refresh(db, vehicle, session, state)
        answer += [move["text"] for move in state["moves"]]
        changed += [move["id"] for move in state["moves"]]
        answer += [p for p in problems if p not in answer]
        if state["trips"] and any(item[:1] in {"B", "J"} for item in changed):
            answer += [w for w in tank_balance(db, vehicle, session, state)["warnings"] if w not in answer]
        if state["start_odo"] is None or state["end_odo"] is None:
            question = "Aký bol stav tachometra " + " a ".join(
                label for key, label in (("start_odo", "na začiatku"), ("end_odo", "na konci")) if state[key] is None) + " mesiaca?"
            if question not in answer:
                answer.append(question)
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


def book_context(db, vehicle, session, state):
    """Rows already saved in the logbook for the month, read-only for the assistant."""
    trips, fuels = saved_rows(db, vehicle, session, state)
    kept_km = sum(t.end_odo - t.start_odo for t in trips)
    close_odo = closing(db, vehicle, session, state)[1]
    left = close_odo - state["start_odo"] - kept_km if state["start_odo"] is not None and close_odo is not None else None
    return dict(
        tankovania=[dict(datum=f.day, stanica=f.station, litre=f.liters, suma=f.gross, tachometer=f.odo, zdroj=f.note)
                    for f in fuels][:60],
        tankovania_litre_spolu=sum((f.liters for f in fuels), D(0)),
        jazdy=[dict(datum=t.day, odkial=t.origin, ciel=t.destination, ucel=t.purpose, typ=t.category,
                    tachometer_od=t.start_odo, tachometer_do=t.end_odo, km=t.end_odo - t.start_odo,
                    mesacny_odhad=bool(t.draft_id)) for t in trips][:80],
        km_ulozenych_jazd=kept_km, km_na_navrh=left)


def destination_history(db, vehicle, month):
    """Most frequent destinations of this vehicle in the six months before, so the assistant can invent a plausible month."""
    since = month_of(month - timedelta(days=183))
    rows = db.execute(select(Trip.destination, Trip.origin, Trip.category, func.count(), func.avg(Trip.end_odo - Trip.start_odo))
                      .where(Trip.vehicle_id == vehicle.id, Trip.day >= since, Trip.day < month, Trip.destination != "")
                      .group_by(Trip.destination, Trip.origin, Trip.category).order_by(func.count().desc()).limit(15)).all()
    return [dict(ciel=d, odkial=o, typ=c, pocet_jazd=n, km_priemer=round(float(k or 0))) for d, o, c, n, k in rows]


def routes_from_history(db, vehicle, session, state):
    """Deterministic month description when the user asks to invent trips and the model returned none: the vehicle's
    frequent destinations (else its catalogue), repeated until they roughly cover the kilometres left to plan."""
    left = book_context(db, vehicle, session, state)["km_na_navrh"]
    if not left or left <= 0:
        return []
    options = [(h["ciel"], h["odkial"], h["typ"], h["km_priemer"]) for h in destination_history(db, vehicle, session.month)
               if h["km_priemer"] > 0][:8]
    if not options:
        options = [(d.name, d.origin, "mesto" if distances.is_city(d.name) else "mimo mesto", d.distance * 2)
                   for d in catalogue(db, vehicle) if d.distance][:8]
    if not options:
        return []
    counts, total, index = [0] * len(options), 0, 0
    first, last = month_bounds(session)
    refuels = len([f for f in db.scalars(select(Fuel).where(Fuel.vehicle_id == vehicle.id, Fuel.day >= first, Fuel.day <= last))
                   if f.odo is None]) + len([r for r in state["receipts"] if r.get("day") and r.get("odo") is None])
    minimum = min(12, refuels + 1)  # one trip per stretch between refuels, so the tank can be planned around them
    while (total < left * 0.9 or sum(counts) < minimum) and sum(counts) < 40:
        counts[index % len(options)] += 1
        total += options[index % len(options)][3]
        index += 1
    return [dict(destination=name[:150], origin=(origin or "")[:150], count=count, approximate_km=km, category=category,
                 purpose="", days=[]) for (name, origin, category, km), count in zip(options, counts) if count]


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
                     suma=r.get("gross"), mena=r.get("currency"), tachometer=r.get("odo"), potvrdeny=r["confirmed"],
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


@router.post("/assistant/{session_id}/confirm-receipts")
def confirm_receipts(session_id: str, db=Db, user=Member):
    session, vehicle, state = session_for(db, user, session_id)
    require_draft(session)
    problems = receipt_checks(db, vehicle, session, state)
    if problems:
        fail(problems[0])
    for receipt in state["receipts"]:
        receipt["confirmed"] = True
    state["receipts_confirmed"] = True
    changed = [r["id"] for r in state["receipts"]]
    text = f"Bločky sú potvrdené ({len(state['receipts'])}). "
    if state["trips"]:
        problems = refresh(db, vehicle, session, state)
        changed += [t["id"] for t in state["trips"]]
        text += " Harmonogram som prepočítal podľa potvrdených tankovaní." + ("\n" + "\n".join(problems) if problems else "")
    elif month_trips(db, vehicle, session.month)["replaceable"] and not state.get("replace_existing"):
        text += " " + existing_text(month_trips(db, vehicle, session.month), session.month, manual=False)
    elif state["routes"] and state["start_odo"] is not None and state["end_odo"] is not None:
        text += " Teraz môžete vytvoriť harmonogram jázd."
    else:
        missing = []
        if state["start_odo"] is None or state["end_odo"] is None:
            missing.append("stav tachometra na začiatku a na konci mesiaca")
        if not state["routes"]:
            missing.append("kde ste približne jazdili")
        text += " Na návrh jázd ešte potrebujem: " + " a ".join(missing) + "."
    say(state, "assistant", text.replace(".  ", ". ") + "\n" + tank_text(db, vehicle, session, state), changed)
    store(session, state, changed)
    db.commit()
    return view(db, vehicle, session)


@router.post("/assistant/{session_id}/plan")
def plan(session_id: str, data: PlanIn, db=Db, user=Member):
    session, vehicle, state = session_for(db, user, session_id)
    require_draft(session)
    try:
        changed = build_balanced(db, vehicle, session, state) if data.rebuild or not state["trips"] else [t["id"] for t in state["trips"]]
        state.pop("added_for_fuel", None)
    except Problem as problem:
        say(state, "assistant", str(problem))
        store(session, state)
        db.commit()
        return view(db, vehicle, session)
    problems = refresh(db, vehicle, session, state)
    estimated = sum(not t["day_confirmed"] for t in state["trips"])
    text = (f"Návrh obsahuje {len(state['trips'])} jázd ({km(sum(t['distance'] for t in state['trips']))}). "
            f"{estimated} dátumov je odhadnutých a vzdialenosti sú dopočítané z tachometra. "
            "Konkrétne dni alebo kilometre mi môžete opraviť v chate, napríklad „V Poprade som bol 14. októbra“.")
    say(state, "assistant", text + ("\n" + "\n".join(problems) if problems else "") + "\n" + tank_text(db, vehicle, session, state), changed)
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
    if state["receipts"] and not (state["receipts_confirmed"] or data.receipts_checked):
        fail("Potvrďte, že ste údaje z bločkov skontrolovali podľa originálov.", 409)
    for receipt in state["receipts"]:
        receipt["confirmed"] = True
    state["receipts_confirmed"] = True
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
                   rate=vehicle.city_rate if trip["category"] == "mesto" else vehicle.outside_rate,
                   estimated=True, draft_id=draft.id)
        db.add(row)
        trips.append(row)
    db.flush()
    result = dict(draft_id=draft.id, trip_ids=[t.id for t in trips], fuel_ids=[f.id for f in fuels],
                  year=session.month.year, month=session.month.month)
    draft.result = result
    session.result = result
    session.status = "saved"
    say(state, "assistant", f"Výkaz je uložený: {len(trips)} jázd a {len(fuels)} tankovaní"
        + (f", nahradených {len(replaced)} skôr uložených odhadov" if replaced else "") + ". Môžete stiahnuť PDF alebo XLSX.")
    store(session, state, [])
    db.add(Audit(user_id=user.id, vehicle_id=vehicle.id, action="Mesačný sprievodca schválený", data=json_value(dict(
        session_id=session.id, draft_id=draft.id, month=session.month, trips=result["trip_ids"], fuels=result["fuel_ids"],
        replaced_trips=replaced,
        model=gemini.model_name()))))
    db.commit()
    return view(db, vehicle, session)

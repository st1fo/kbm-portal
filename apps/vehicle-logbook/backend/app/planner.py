"""Deterministic reconstruction from user-provided routes and odometer checkpoints."""
import hashlib
import json
import unicodedata
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select

from .db import Closing, Destination, Fuel, Holiday, Trip
from .ledger import month_of, next_month

class PlanError(ValueError):
    pass


def fingerprint(db, vehicle):
    def values(row):
        return {column.name: str(getattr(row, column.name)) for column in row.__table__.columns}
    data = {"vehicle": values(vehicle)}
    for model in (Trip, Fuel, Closing):
        rows = [values(row) for row in db.scalars(select(model).where(model.vehicle_id == vehicle.id))]
        data[model.__tablename__] = sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def month_context(db, vehicle, month, start_odo, end_odo, today, extra=(), replaceable=False, keep_manual=False):
    """Validate the month and return odometer anchors; `extra` holds (day, odo) from unsaved receipts.
    With `replaceable`, trips of this month saved earlier from a monthly draft are ignored (they get replaced).
    With `keep_manual`, manually entered trips of the month may stay (the caller plans around them)."""
    if month >= month_of(today):
        raise PlanError("Mesačný návrh vytvorte po skončení mesiaca.")
    stop = next_month(month)
    latest = db.scalar(select(Closing.month).where(Closing.vehicle_id == vehicle.id)
                       .order_by(Closing.month.desc()).limit(1))
    if (latest and month <= latest) or (vehicle.imported_lock and stop - timedelta(days=1) <= vehicle.imported_lock):
        raise PlanError("Obdobie je uzavreté. Najprv ho musí správca otvoriť.")
    if stop <= vehicle.start_date:
        raise PlanError("Vybraný mesiac predchádza začiatku evidencie vozidla.")
    trips = list(db.scalars(select(Trip).where(Trip.vehicle_id == vehicle.id)
                           .order_by(Trip.day, Trip.end_odo, Trip.id)))
    if replaceable:
        trips = [trip for trip in trips if not (month <= trip.day < stop and trip.draft_id)]
    if any(month <= trip.day < stop and (trip.draft_id or not keep_manual) for trip in trips):
        raise PlanError("Mesiac už obsahuje jazdy. Návrh je určený pre mesiac bez zapísaných jázd.")
    previous = [trip for trip in trips if trip.day < month]
    following = [trip for trip in trips if trip.day >= stop]
    expected = previous[-1].end_odo if previous else vehicle.initial_odo
    if expected != start_odo:
        raise PlanError(f"Počiatočný tachometer musí nadväzovať na evidenciu, očakáva sa {expected} km.")
    if following and following[0].start_odo != end_odo:
        raise PlanError(f"Nasledujúci mesiac začína na {following[0].start_odo} km. Skontrolujte koncový stav.")
    fuels = [(fuel.day, fuel.odo) for fuel in db.scalars(select(Fuel).where(Fuel.vehicle_id == vehicle.id,
        Fuel.day >= month, Fuel.day < stop, Fuel.odo.is_not(None)))]
    checkpoints = []
    last = start_odo
    for day, odo in sorted({*fuels, *extra}):
        if not last <= odo <= end_odo or day < vehicle.start_date:
            raise PlanError("Tachometre tankovaní sú mimo rozsahu alebo nie sú chronologické. Opravte doklady pred návrhom.")
        last = odo
        checkpoints.append((day, odo))
    checkpoints.append((stop - timedelta(days=1), end_odo))
    return checkpoints


def normalized(value):
    return ''.join(char for char in unicodedata.normalize('NFKD', value.casefold())
                   if not unicodedata.combining(char)).strip()


def build_plan(db, vehicle, data, today):
    checkpoints = month_context(db, vehicle, data.month, data.start_odo, data.end_odo, today)
    catalogue = list(db.scalars(select(Destination).where(Destination.vehicle_id == vehicle.id)))
    warnings = ["Dátumy a kilometre sú navrhnuté z približných podkladov. Pred uložením overte každú jazdu."]
    routes = []
    for route in data.routes:
        values = route.model_dump()
        if route.approximate_km is None:
            matches = [item for item in catalogue if normalized(item.name) == normalized(route.destination)
                       and (not route.origin or normalized(item.origin) == normalized(route.origin))]
            if len(matches) != 1:
                raise PlanError(f"Pre cieľ {route.destination} zadajte približné kilometre jednej celej jazdy alebo vyberte známy cieľ.")
            values.update(approximate_km=matches[0].distance * 2,
                          origin=route.origin or matches[0].origin)
        routes.append(values)
    # Spread repeated routes through the occurrence list in a stable round-robin order.
    occurrences = []
    for repetition in range(max(route['count'] for route in routes)):
        for route in routes:
            if repetition < route['count']:
                occurrences.append(route)
    segments = []
    prior_day, prior_odo = max(data.month, vehicle.start_date), data.start_odo
    for day, odo in checkpoints:
        if odo > prior_odo:
            segments.append((prior_day, day, prior_odo, odo))
        prior_day, prior_odo = day, odo
    if len(segments) > len(occurrences):
        raise PlanError(f"Tankovania rozdeľujú mesiac na {len(segments)} úsekov s jazdou. Doplňte aspoň toľko jázd do trás.")
    budgets = [end - start for _, _, start, end in segments]
    if len(occurrences) > sum(budgets):
        raise PlanError("Počet jázd presahuje počet kilometrov. Znížte počet opakovaní.")
    # Allocate at least one occurrence per positive checkpoint segment, weighted by distance.
    counts = [1] * len(segments)
    for _ in range(len(occurrences) - len(segments)):
        candidates = [i for i, budget in enumerate(budgets) if counts[i] < budget]
        index = max(candidates, key=lambda i: (Decimal(budgets[i]) / (counts[i] + 1), -i))
        counts[index] += 1
    holidays = set(db.scalars(select(Holiday.day).where(Holiday.vehicle_id == vehicle.id)))
    rows = []
    position = 0
    for segment, count in zip(segments, counts):
        first_day, last_day, start, end = segment
        batch = occurrences[position:position + count]
        position += count
        # Largest-remainder allocation produces exact integer kilometers and positive rows.
        remaining = end - start - count
        weights = [Decimal(route['approximate_km']) for route in batch]
        quotas = [Decimal(remaining) * weight / sum(weights) for weight in weights]
        distances = [1 + int(quota) for quota in quotas]
        missing = end - start - sum(distances)
        order = sorted(range(count), key=lambda i: (quotas[i] - int(quotas[i]), -i), reverse=True)
        for index in order[:missing]:
            distances[index] += 1
        dates = [first_day + timedelta(days=i) for i in range((last_day - first_day).days + 1)]
        dates = [day for day in dates if day.weekday() < 5 and day not in holidays] or [last_day]
        for index, (route, distance) in enumerate(zip(batch, distances)):
            day = dates[min(len(dates) - 1, ((index + 1) * len(dates) - 1) // count)]
            if abs(Decimal(distance) / route['approximate_km'] - 1) > Decimal('0.3'):
                warnings.append(f"{route['destination']}: návrh {distance} km sa líši o viac než 30 % od zadaných {route['approximate_km']} km na jazdu.")
            rate = vehicle.city_rate if route['category'] == 'mesto' else vehicle.outside_rate
            rows.append(dict(day=day.isoformat(), start_odo=start, end_odo=start + distance,
                origin=route['origin'], destination=route['destination'], category=route['category'],
                purpose=route['purpose'], note=data.note, rate=str(rate), distance=distance,
                approximate_km=route['approximate_km']))
            start += distance
    return dict(month=data.month.isoformat(), start_odo=data.start_odo, end_odo=data.end_odo,
                distance=data.end_odo - data.start_odo, warnings=list(dict.fromkeys(warnings)), rows=rows,
                checkpoints=[dict(day=day.isoformat(), odo=odo) for day, odo in checkpoints[:-1]])


def validate_review(db, vehicle, draft, rows, today):
    payload = draft.payload
    checkpoints = month_context(db, vehicle, draft.month, payload['start_odo'], payload['end_odo'], today)
    stop = next_month(draft.month)
    previous = payload['start_odo']
    last_day = max(draft.month, vehicle.start_date)
    for row in rows:
        if not last_day <= row.day < stop:
            raise PlanError("Dátumy musia byť v zvolenom mesiaci a jazdy zoradené chronologicky.")
        if row.start_odo != previous or row.end_odo <= previous:
            raise PlanError("Jazdy musia nadväzovať a každá musí mať kladnú vzdialenosť.")
        if not row.destination.strip():
            raise PlanError("Pri každej jazde doplňte cieľ.")
        previous, last_day = row.end_odo, row.day
    if previous != payload['end_odo']:
        raise PlanError("Súčet jázd sa musí rovnať rozdielu mesačných tachometrov.")
    for day, odo in checkpoints[:-1]:
        preceding = [row for row in rows if (row.day, row.end_odo) <= (day, odo)]
        actual = preceding[-1].end_odo if preceding else payload['start_odo']
        if actual != odo:
            raise PlanError(f"Jazdy nenadväzujú na doklad tankovania z {day.isoformat()} pri {odo} km. Upravte dátumy alebo vzdialenosti.")

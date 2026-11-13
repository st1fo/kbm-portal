from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select
from .db import Closing, Fuel, Holiday, Trip

D = Decimal
ZERO = D(0)

def tank_tolerance(vehicle):
    """How far the tank by the norm may go below empty or above full before a warning (real consumption varies)."""
    return max(Decimal(1), Decimal(vehicle.capacity) * Decimal("0.03"))


def rounded(value, quantum="0.01"):
    return value.quantize(D(quantum), rounding=ROUND_HALF_UP)

def next_month(day):
    return date(day.year + (day.month == 12), day.month % 12 + 1, 1)

def month_of(day):
    return day.replace(day=1)

def fuel_values(fuel):
    gross = rounded(fuel.gross)
    net = rounded(fuel.gross / (1 + fuel.vat / 100))
    return {"gross": gross, "net": net, "vat_amount": gross - net,
            "unit_price": rounded(fuel.gross/fuel.liters, "0.0001")}

def events(db, vehicle):
    trips = db.scalars(select(Trip).where(Trip.vehicle_id == vehicle.id))
    purchases = db.scalars(select(Fuel).where(Fuel.vehicle_id == vehicle.id))
    combined = [(r.day, r.end_odo, 0, "", r.id, r) for r in trips]
    # A receipt without an odometer goes after the day's trips, before 11:00 before them (Excel rule); its reading stays empty.
    combined += [(r.day, r.odo if r.odo is not None else (-1 if "" < (r.clock or "") < "11:00" else 10**12), 1, r.clock, r.id, r)
                 for r in purchases]
    return [item[-1] for item in sorted(combined)]

def frozen_until(db, vehicle):
    """Last day whose rows stay as recorded (closed months, imported Excel period); later refuels fill the tank."""
    last = db.scalar(select(func.max(Closing.month)).where(Closing.vehicle_id == vehicle.id))
    days = [d for d in (last and next_month(last) - timedelta(days=1), vehicle.imported_lock) if d]
    return max(days) if days else date.min

def norm_rate(vehicle, category):
    return vehicle.city_rate if category == "mesto" else vehicle.outside_rate

def window_rates(vehicle, items, liters):
    """Rates of the trips up to the next refuel (Excel FillFuelTank): every refuel fills the tank (or to the measured level),
    so the trips burn start tank − (full − refuel litres), split by km with city km weighted by the norm ratio.
    Without a closing refuel the norm applies (estimate)."""
    stop = next((i for i, e in enumerate(items) if not isinstance(e, Trip)), len(items))
    trips, fuel = items[:stop], items[stop] if stop < len(items) else None
    if fuel is None:
        return {t.id: norm_rate(vehicle, t.category) for t in trips}
    alpha = D(vehicle.city_rate) / D(vehicle.outside_rate)
    weighted = sum((D(t.end_odo - t.start_odo) * (alpha if t.category == "mesto" else 1) for t in trips), ZERO)
    after = fuel.measured if fuel.measured is not None else D(vehicle.capacity)
    burned = max(liters - (after - fuel.liters), ZERO)
    outside = burned * 100 / weighted if weighted else ZERO
    return {t.id: outside * alpha if t.category == "mesto" else outside for t in trips}

def ledger(db, vehicle):
    odo = vehicle.initial_odo
    liters, net = vehicle.initial_liters, vehicle.initial_net
    holidays = set(db.scalars(select(Holiday.day).where(Holiday.vehicle_id == vehicle.id)))
    frozen = frozen_until(db, vehicle)
    items = events(db, vehicle)
    rates = {}
    window = {"km": 0, "consumed": ZERO, "norm": ZERO}
    output = []
    for index, event in enumerate(items):
        live = event.day > frozen
        row = {"id": event.id, "day": event.day.isoformat(), "start_odo": odo, "start_liters": liters,
               "start_net": net, "warnings": [], "correction": ZERO, "distance": 0,
               "consumed": ZERO, "purchased": ZERO, "gross": ZERO, "purchase_net": ZERO,
               "norm": ZERO, "nonworking": event.day.weekday() >= 5 or event.day in holidays}
        if isinstance(event, Trip):
            if live and event.id not in rates:
                rates.update(window_rates(vehicle, items[index:], liters))
            rate = rates[event.id] if live else event.rate
            distance = event.end_odo - event.start_odo
            consumed = rate * distance / 100
            norm = norm_rate(vehicle, event.category) * distance / 100
            if odo != event.start_odo:
                row["warnings"].append("Tachometer jazdy nenadväzuje na predchádzajúci záznam.")
            if consumed and consumed > liters + tank_tolerance(vehicle):
                row["warnings"].append("Vypočítaný stav nádrže pred tankovaním je záporný.")
            if liters > 0:
                net -= min(consumed, liters) * net / liters
            liters -= consumed
            odo = event.end_odo
            window["km"] += distance; window["consumed"] += consumed; window["norm"] += norm
            row.update(type="trip", distance=distance, consumed=consumed, norm=norm,
                       start_odo=event.start_odo, category=event.category, rate=rounded(D(rate), "0.001"),
                       origin=event.origin, destination=event.destination, purpose=event.purpose,
                       estimated=event.estimated, legacy_row=event.legacy_row, note=event.note,
                       source="Mesačný odhad" if event.draft_id else ("Odhad Excelu" if event.estimated else "Zadaná jazda"))
        else:
            if event.odo is None:
                pass
            elif event.odo < odo:
                row["warnings"].append("Tachometer pri tankovaní je nižší než predošlý stav.")
            elif event.odo > odo:
                row["warnings"].append("Pred tankovaním chýba jazda s rovnakým koncovým tachometrom.")
            # A receipt reading can flag missing driving without creating fictional kilometers.
            liters += event.liters
            purchase_net = rounded(event.gross / (1 + event.vat / 100))   # per receipt, so totals match the listed rows
            net += purchase_net
            target = event.measured if event.measured is not None else (D(vehicle.capacity) if live else None)
            if live and window["km"] == 0 and index > 0:
                row["warnings"].append("Medzi tankovaniami nie sú žiadne jazdy – doplňte jazdy pred týmto tankovaním.")
            elif live and window["km"] and event.measured is None and liters > target + tank_tolerance(vehicle):
                row["warnings"].append(f"{rounded(event.liters)} l sa nezmestí do nádrže ({vehicle.capacity} l) – "
                                       "skontrolujte objem tankovania alebo zadajte zmeraný stav nádrže.")
            elif live and window["km"]:
                per100 = window["consumed"] * 100 / window["km"]
                norm100 = window["norm"] * 100 / window["km"]
                if per100 > norm100 + D("0.05"):
                    row["warnings"].append(f"Spotreba {rounded(per100, '0.1')} l/100 km je nad normou {rounded(norm100, '0.1')} – "
                                           "pravdepodobne chýbajú jazdy pred týmto tankovaním.")
                elif per100 < norm100 * D("0.7"):
                    row["warnings"].append(f"Spotreba {rounded(per100, '0.1')} l/100 km je výrazne pod normou {rounded(norm100, '0.1')} – "
                                           "skontrolujte, či nie je priveľa km.")
            if target is not None:
                correction = target - liters
                if abs(correction) < D("0.000001"):
                    correction = ZERO
                row["correction"] = correction
                if target == 0:
                    net = ZERO
                elif correction < 0 and liters > 0:
                    net *= target / liters
                liters = target
            if liters > vehicle.capacity + tank_tolerance(vehicle) or liters < -tank_tolerance(vehicle):
                row["warnings"].append("Stav nádrže je mimo rozsahu vozidla.")
            window = {"km": 0, "consumed": ZERO, "norm": ZERO}
            row.update(type="fuel", odo=event.odo, purchased=event.liters, purchase_net=purchase_net,
                       vat=event.vat, station=event.station, clock=event.clock, note=event.note,
                       measured=event.measured, legacy_row=event.legacy_row,
                       source="Bloček zo sprievodcu" if event.draft_id else "Zadaný doklad", **fuel_values(event))
        row.update(end_odo=odo, end_liters=liters, end_net=net)
        output.append(row)
    per_refuel(output)
    return output

def per_refuel(rows):
    """l/100 km from refuel to refuel: litres burned since the previous refuel over the km (the tank is full after every
    refuel, so that is the refuel's litres), on the refuel with `window_km`. Each trip shows its own rate, which is that
    average (city km weighted by the norm ratio); trips not closed by a refuel yet use the norm and get `per100_estimate`."""
    window = []
    for row in rows:
        row["per100"], row["per100_estimate"] = None, False
        if row["type"] == "trip":
            row["per100"] = rounded(row["rate"])
            window.append(row)
            continue
        km = sum(r["distance"] for r in window)
        if km > 0:
            row["per100"] = rounded(sum((r["consumed"] for r in window), ZERO) * 100 / km)
            row["window_km"] = km
        window = []
    for trip in window:
        trip["per100_estimate"] = True

def report(vehicle, rows, month):
    start, stop = month.isoformat(), next_month(month).isoformat()
    prior = [r for r in rows if r["day"] < start]
    selected = [r for r in rows if start <= r["day"] < stop]
    opening = prior[-1] if prior else {"end_liters": vehicle.initial_liters,
        "end_net": vehicle.initial_net, "end_odo": vehicle.initial_odo}
    closing = selected[-1] if selected else opening
    trips = [r for r in selected if r["type"] == "trip"]
    def total(key):
        return sum((r[key] for r in selected), ZERO)
    purchased = total("purchased")
    gross = total("gross")
    consumption = opening["end_liters"] + purchased - closing["end_liters"]
    distance = sum(r["distance"] for r in trips)
    return dict(month=start[:7], registration=vehicle.registration, vehicle=vehicle.name,
                responsible=vehicle.responsible, kind=vehicle.kind,
                opening_odo=opening["end_odo"], closing_odo=closing["end_odo"], distance=distance,
                city_km=sum(r["distance"] for r in trips if r["category"] == "mesto"),
                outside_km=sum(r["distance"] for r in trips if r["category"] == "mimo mesto"),
                trip_count=len(trips), estimated_count=sum(bool(r["estimated"]) for r in trips), purchased=rounded(purchased), gross=rounded(gross),
                purchase_net=rounded(total("purchase_net")), opening_liters=rounded(opening["end_liters"]),
                closing_liters=rounded(closing["end_liters"]), opening_net=rounded(opening["end_net"]),
                closing_net=rounded(closing["end_net"]), normative=rounded(total("norm")),
                calculated=rounded(total("consumed")), consumption=rounded(consumption),
                consumption_net=rounded(opening["end_net"] + total("purchase_net") - closing["end_net"]),
                average=rounded(consumption * 100 / distance) if distance else ZERO,
                gross_per_liter=rounded(gross/purchased, "0.0001") if purchased else ZERO,
                correction=rounded(total("correction")), rows=selected)

def months(db, vehicle, year):
    data = ledger(db, vehicle)
    snapshots = {c.month: c.snapshot for c in db.scalars(select(Closing).where(
        Closing.vehicle_id == vehicle.id, Closing.month >= date(year, 1, 1), Closing.month <= date(year, 12, 1)))}
    # a closed month keeps its snapshot, but the refuel-to-refuel average stays live (a later refuel settles it)
    live = {(r["type"], r["id"]): r for r in data}
    for snapshot in snapshots.values():
        snapshot["rows"] = [{**row, **{key: live[row["type"], row["id"]].get(key) for key in ("per100", "per100_estimate", "window_km")}}
                            if (row.get("type"), row.get("id")) in live else row for row in snapshot.get("rows", [])]
    result = []
    for number in range(1, 13):
        month = date(year, number, 1)
        result.append({**(snapshots[month] if month in snapshots else report(vehicle, data, month)),
                       "closed": month in snapshots or bool(vehicle.imported_lock and next_month(month) - timedelta(days=1) <= vehicle.imported_lock)})
    return result

def year_totals(data):
    """Ročné súčty z 12 mesiacov (months); priemery z celkových súčtov, nie priemer mesačných priemerov."""
    def total(key):
        return sum((D(str(month.get(key) or 0)) for month in data), ZERO)
    distance, gross, consumption, normative = total("distance"), total("gross"), total("consumption"), total("normative")
    rows = [row for month in data for row in month.get("rows", [])]
    return dict(distance=int(distance), gross=rounded(gross), purchased=rounded(total("purchased")),
                consumption=rounded(consumption), normative=rounded(normative),
                average=rounded(consumption * 100 / distance) if distance else ZERO,
                norm=rounded(normative * 100 / distance) if distance else ZERO,
                per_km=rounded(gross / distance, "0.001") if distance else ZERO,
                refuels=sum(row.get("type") == "fuel" for row in rows), trip_count=int(total("trip_count")),
                estimated_count=int(total("estimated_count")), warnings=sum(len(row.get("warnings") or []) for row in rows))

def overview(db, vehicles, year):
    """Porovnanie vozidiel za rok: súhrn každého vozidla a „Spolu“ za celý vozový park."""
    items = [{"id": v.id, "name": v.name, "registration": v.registration, **year_totals(months(db, v, year))} for v in vehicles]
    fleet = year_totals(items)   # súčty vozidiel majú rovnaké kľúče ako mesiace (bez riadkov)
    for key in ("refuels", "warnings"):
        fleet[key] = sum(item[key] for item in items)
    return {"year": year, "vehicles": items, "total": fleet}

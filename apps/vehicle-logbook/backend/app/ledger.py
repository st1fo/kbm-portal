from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import select
from .db import Closing, Fuel, Holiday, Trip

D = Decimal
ZERO = D(0)

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
    # A receipt without an odometer is placed after the day's trips; its reading stays empty.
    combined += [(r.day, r.odo if r.odo is not None else 10**12, 1, r.clock, r.id, r) for r in purchases]
    return [item[-1] for item in sorted(combined)]

def ledger(db, vehicle):
    odo = vehicle.initial_odo
    liters, net = vehicle.initial_liters, vehicle.initial_net
    holidays = set(db.scalars(select(Holiday.day).where(Holiday.vehicle_id == vehicle.id)))
    output = []
    for event in events(db, vehicle):
        row = {"id": event.id, "day": event.day.isoformat(), "start_odo": odo, "start_liters": liters,
               "start_net": net, "warnings": [], "correction": ZERO, "distance": 0,
               "consumed": ZERO, "purchased": ZERO, "gross": ZERO, "purchase_net": ZERO,
               "norm": ZERO, "nonworking": event.day.weekday() >= 5 or event.day in holidays}
        if isinstance(event, Trip):
            distance = event.end_odo - event.start_odo
            consumed = event.rate * distance / 100
            norm = (vehicle.city_rate if event.category == "mesto" else vehicle.outside_rate) * distance / 100
            if odo != event.start_odo:
                row["warnings"].append("Tachometer jazdy nenadväzuje na predchádzajúci záznam.")
            if consumed and consumed > liters:
                row["warnings"].append("Vypočítaný stav nádrže pred tankovaním je záporný.")
            if liters > 0:
                net -= min(consumed, liters) * net / liters
            liters -= consumed
            odo = event.end_odo
            row.update(type="trip", distance=distance, consumed=consumed, norm=norm,
                       start_odo=event.start_odo, category=event.category, rate=event.rate,
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
            purchase_net = event.gross / (1 + event.vat / 100)
            net += purchase_net
            if event.measured is not None:
                correction = event.measured - liters
                if abs(correction) < D("0.000001"):
                    correction = ZERO
                row["correction"] = correction
                if event.measured == 0:
                    net = ZERO
                elif correction < 0 and liters > 0:
                    net *= event.measured / liters
                liters = event.measured
            if liters > vehicle.capacity or liters < 0:
                row["warnings"].append("Stav nádrže je mimo rozsahu vozidla.")
            row.update(type="fuel", odo=event.odo, purchased=event.liters, purchase_net=purchase_net,
                       vat=event.vat, station=event.station, clock=event.clock, note=event.note,
                       measured=event.measured, legacy_row=event.legacy_row,
                       source="Bloček zo sprievodcu" if event.draft_id else "Zadaný doklad", **fuel_values(event))
        row.update(end_odo=odo, end_liters=liters, end_net=net)
        output.append(row)
    return output

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
    result = []
    for number in range(1, 13):
        month = date(year, number, 1)
        result.append({**(snapshots[month] if month in snapshots else report(vehicle, data, month)),
                       "closed": month in snapshots or bool(vehicle.imported_lock and next_month(month) - timedelta(days=1) <= vehicle.imported_lock)})
    return result

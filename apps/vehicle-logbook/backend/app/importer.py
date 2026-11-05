from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from zipfile import ZipFile

from openpyxl import load_workbook
from pydantic import ValidationError

from .schemas import DestinationIn, FuelIn, TripIn, VehicleIn

def day(value):
    if not isinstance(value, datetime):
        raise ValueError("Chýba uložený dátum. Uložte prepočítaný zošit v Exceli.")
    return value.date()

def dec(value):
    return Decimal(str(value)).quantize(Decimal("0.0000000001")).normalize()

def integer(value):
    result = int(value)
    if isinstance(value, bool) or result != value:
        raise ValueError("Očakáva sa celé číslo.")
    return result

def parse(content):
    with ZipFile(BytesIO(content)) as archive:
        if len(archive.infolist()) > 300 or sum(i.file_size for i in archive.infolist()) > 45_000_000:
            raise ValueError("Zošit je príliš veľký.")
    workbook = load_workbook(BytesIO(content), read_only=True, data_only=True, keep_links=False)
    try:
        return read(workbook, sha256(content).hexdigest())
    finally:
        workbook.close()

def read(w, digest):
    required = {"cesty", "nastavenia", "miesta", "sviatky"}
    if not required.issubset(w.sheetnames):
        raise ValueError("Chýbajú hárky pôvodnej šablóny.")
    s = dict((i, r[1]) for i, r in enumerate(w["nastavenia"].iter_rows(max_row=13, max_col=2, values_only=True), 1))
    registration = str(s[2] or "").strip().upper()
    config = VehicleIn(name=registration, registration=registration, responsible=str(s[3] or ""),
        kind=str(s[4] or ""), initial_odo=s[5], initial_liters=dec(s[6]), initial_net=dec(s[7]),
        city_rate=dec(s[8]), outside_rate=dec(s[9]), capacity=dec(s[10]), city_limit=s[11],
        start_date=day(s[12])).model_dump()
    config["imported_lock"] = day(s[13]) if s.get(13) else None
    trips, purchases, destinations, warnings = [], [], [], []
    previous = config["initial_odo"]
    last_day = config["start_date"]
    for i, r in enumerate(w["cesty"].iter_rows(min_row=2, max_col=32, values_only=True), 2):
        a,b,c,d,e,f,g,h,liters,gross,vat,clock,station,n,rate,p,q,opening,ending,t,u,*flags = r
        if not any(v is not None and v != "" and v != 0 for v in (g, liters, gross)) and not (
                isinstance(f, (int, float)) and f != e):
            continue
        try:
            dt = day(a)
            if dt < last_day:
                raise ValueError("Dátumy nie sú chronologické.")
            start, end = integer(e), integer(f)
            if start != previous or end < start or end - start != integer(g):
                raise ValueError("Tachometer a vzdialenosť nenadväzujú. Riadok vynechaný.")
        except (ValueError, TypeError, OverflowError) as error:
            warnings.append(f"cesty, riadok {i}. {error}")
            continue
        trip = None
        if end > start:
            try:
                estimated = not bool(r[29])
                if estimated:
                    warnings.append(f"cesty, riadok {i}. Vzdialenosť nebola v Exceli ručne fixovaná.")
                if q in ("#DIV/0!", "#VALUE!", "#REF!") or u in ("#DIV/0!", "#VALUE!", "#REF!"):
                    warnings.append(f"cesty, riadok {i}. Chybná finančná hodnota bude prepočítaná.")
                if not isinstance(rate,(int,float)) or rate <= 0:
                    warnings.append(f"cesty, riadok {i}. Neplatná spotreba nahradená normou.")
                    rate = config["city_rate" if d == "mesto" else "outside_rate"]
                trip = TripIn(day=dt, start_odo=start, end_odo=end, category=d, destination=str(b or ""),
                    note=str(c or ""), rate=dec(rate)).model_dump()
                trip.update(legacy_row=i, estimated=estimated)
            except (ValueError, TypeError):
                warnings.append(f"cesty, riadok {i}. Neplatné údaje jazdy, riadok vynechaný.")
                continue
        if trip:
            trips.append(trip)
        previous, last_day = end, dt
        if liters or gross:
            try:
                measured = dec(ending) if isinstance(ending,(int,float)) and 0 <= ending <= config["capacity"] else None
                if measured is None:
                    warnings.append(f"cesty, riadok {i}. Stav nádrže po tankovaní chýba alebo je mimo rozsahu.")
                value = FuelIn(day=dt, odo=end, liters=dec(liters), gross=dec(gross),
                    vat=dec(vat)*100 if vat is not None else (23 if dt.year >= 2025 else 20),
                    clock=clock, station=str(station or ""), measured=measured)
                record = value.model_dump(exclude={"unit_price","clock"})
                record.update(legacy_row=i, clock=value.clock.isoformat() if value.clock else "")
                purchases.append(record)
            except (ValueError, TypeError, ArithmeticError):
                warnings.append(f"cesty, riadok {i}. Neplatné tankovanie, záznam vynechaný.")
    seen = set()
    for i, (name, km) in enumerate(w["miesta"].iter_rows(min_row=2, max_col=2, values_only=True), 2):
        if name is None and km is None:
            continue
        try:
            dest = DestinationIn(name=str(name or ""), distance=km)
            if dest.name.casefold() in seen:
                warnings.append(f"miesta, riadok {i}. Duplicitný cieľ vynechaný.")
                continue
            seen.add(dest.name.casefold())
            destinations.append(dest.model_dump())
        except ValidationError:
            warnings.append(f"miesta, riadok {i}. Neplatný cieľ vynechaný.")
    holidays = sorted({r[0].date() for r in w["sviatky"].iter_rows(max_col=1, values_only=True)
                       if isinstance(r[0], datetime)})
    return dict(digest=digest, vehicle=config, trips=trips, purchases=purchases,
                destinations=destinations, holidays=holidays, warnings=warnings)

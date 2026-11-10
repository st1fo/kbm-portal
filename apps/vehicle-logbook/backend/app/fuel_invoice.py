"""Súhrnná faktúra z tankovacích kariet (FleetCor / Shell Card) → tankovania rozdelené podľa ŠPZ.

Čítanie je deterministické (text PDF cez pdfplumber), bez AI. Súčty každej karty aj celej faktúry sa musia zhodovať
s načítanými riadkami, inak sa faktúra odmietne. Riadky, ktoré sa nezapíšu, majú vždy uvedený dôvod.
"""
import hashlib
import io
import re
import unicodedata
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import func, select

from .db import Closing, Fuel, FuelInvoice, Vehicle
from .ledger import month_of

NUM = r"-?\d{1,3}(?:\.\d{3})*,\d+"
ROW = re.compile(rf"^(?P<odo>\d+) (?P<day>\d\d\.\d\d\.\d{{4}}) (?P<clock>\d\d:\d\d) (?P<voucher>\d+) (?P<middle>.+?) "
                 rf"(?P<liters>{NUM}) (?P<discount>{NUM}) (?P<unit>{NUM}) (?P<net>{NUM}) (?P<rate>{NUM}) (?P<vat>{NUM}) (?P<gross>{NUM})$")
CARD_TOTAL = re.compile(rf"^Karta celkom (?P<net>{NUM}) (?P<vat>{NUM}) (?P<gross>{NUM})$")
GRAND_TOTAL = re.compile(rf"^Celkom za všetky karty (?P<net>{NUM}) (?P<vat>{NUM}) (?P<gross>{NUM})$")
PRODUCT = re.compile(rf"^(?P<name>.+?) (?P<qty>{NUM}) .*?(?P<net>{NUM}) (?P<rate>{NUM}) (?P<vat>{NUM}) (?P<gross>{NUM})$")
FUEL_WORDS = ("nafta", "diesel", "natural", "benzin", "benzín", "super", "lpg", "cng", "v-power", "maxxmotion", "evo")


class InvoiceError(ValueError):
    pass


def dec(text: str) -> Decimal:
    return Decimal(text.replace(".", "").replace(",", "."))


def plate_key(text: str) -> str:
    """ŠPZ bez medzier, pomlčiek a diakritiky, veľkými písmenami (ZA-395 JN = ZA395JN)."""
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def fuel_kind(text: str) -> str:
    t = (text or "").casefold()
    if any(w in t for w in ("nafta", "diesel")): return "nafta"
    if any(w in t for w in ("natural", "benzín", "benzin", "95", "98", "100")): return "benzín"
    if "lpg" in t: return "LPG"
    if "cng" in t: return "CNG"
    return ""


def is_fuel(product: str) -> bool:
    return bool(fuel_kind(product)) or any(w in (product or "").casefold() for w in FUEL_WORDS)


def lines_of(content: bytes) -> list[str]:
    import pdfplumber
    try:
        with pdfplumber.open(io.BytesIO(content)) as pdf:
            return [line.strip() for page in pdf.pages for line in (page.extract_text(x_tolerance=1) or "").splitlines()]
    except Exception as error:  # poškodené alebo zašifrované PDF
        raise InvoiceError("PDF sa nedá prečítať. Nahrajte pôvodnú faktúru z portálu FleetCor.") from error


def parse(content: bytes) -> dict:
    if not content.startswith(b"%PDF"):
        raise InvoiceError("Vyberte faktúru vo formáte PDF.")
    lines = lines_of(content)
    text = "\n".join(lines)
    number = re.search(r"Číslo faktúry (\S+)", text)
    if "FleetCor" not in text or not number:
        raise InvoiceError("Toto nie je súhrnná faktúra FleetCor (Shell Card). Iné faktúry zatiaľ nevieme načítať.")
    issued = re.search(r"Dátum faktúry (\d\d\.\d\d\.\d{4})", text)
    products, in_products = [], False
    for line in lines:
        if line.startswith("Prehľad produktov"):
            in_products = True
        elif in_products and line.startswith("Celkom"):
            in_products = False
        elif in_products and (m := PRODUCT.match(line)):
            products.append(m["name"])
    products.sort(key=len, reverse=True)

    cards, card, expect_card, grand = [], None, False, None
    for line in lines:
        if line.startswith("Číslo karty"):
            expect_card = True
            continue
        if expect_card:
            expect_card = False
            parts = line.split()
            if parts and parts[0].isdigit() and len(parts[0]) >= 12:
                plate = parts[-1] if len(parts) > 1 and re.fullmatch(r"[A-Z0-9-]{5,10}", parts[-1]) and re.search(r"\d", parts[-1]) else ""
                driver = " ".join(parts[1:-1] if plate else parts[1:]).title()
                card = {"card": parts[0], "plate": plate, "driver": driver, "rows": [], "total": None}
                cards.append(card)
                continue
        m = ROW.match(line)
        if m and card is not None:
            middle = m["middle"]
            product = next((p for p in products if middle.endswith(" " + p) or middle == p), "")
            station = middle[: len(middle) - len(product)].strip() if product else middle
            card["rows"].append({
                "voucher": m["voucher"], "day": datetime.strptime(m["day"], "%d.%m.%Y").date().isoformat(), "clock": m["clock"],
                "station": station, "product": product, "liters": dec(m["liters"]), "net": dec(m["net"]),
                "rate": dec(m["rate"]), "vat": dec(m["vat"]), "gross": dec(m["gross"]), "odo": int(m["odo"]) or None})
            continue
        m = CARD_TOTAL.match(line)
        if m and card is not None:
            card["total"] = dec(m["gross"])
            continue
        m = GRAND_TOTAL.match(line)
        if m: grand = dec(m["gross"])
    rows = [r for c in cards for r in c["rows"]]
    if not rows or grand is None:
        raise InvoiceError("Vo faktúre sa nenašli tankovania. Nahrajte súhrnnú faktúru s rozpisom po kartách.")
    # kontrola úplnosti: každá karta aj celá faktúra musí sedieť na cent
    for c in cards:
        total = sum((r["gross"] for r in c["rows"]), Decimal(0))
        if c["total"] is None or total != c["total"]:
            raise InvoiceError(f"Súčet karty {c['plate'] or c['card']} nesedí ({total} € ≠ {c['total']} €). Faktúra sa nenačítala celá.")
    if sum((r["gross"] for r in rows), Decimal(0)) != grand:
        raise InvoiceError("Súčet tankovaní nesedí s celkovou sumou faktúry. Faktúra sa nenačítala celá.")
    return {"supplier": "FleetCor", "number": number[1],
            "issued": datetime.strptime(issued[1], "%d.%m.%Y").date().isoformat() if issued else None,
            "total": grand, "cards": cards, "digest": hashlib.sha256(content).hexdigest()}


def check(db, parsed: dict) -> dict:
    """Ku každému riadku doplní vozidlo, stav (zapíše sa / preskočí sa + dôvod) a upozornenia."""
    vehicles = {plate_key(v.registration): v for v in db.scalars(select(Vehicle))}
    closed = dict(db.execute(select(Closing.vehicle_id, func.max(Closing.month)).group_by(Closing.vehicle_id)).all())
    existing = db.get(FuelInvoice, parsed["number"])
    seen: set = set()
    write = skip = 0
    for c in parsed["cards"]:
        v = vehicles.get(plate_key(c["plate"])) if c["plate"] else None
        c["vehicle"] = {"id": v.id, "registration": v.registration, "name": v.name, "fuel_type": v.fuel_type} if v else None
        for r in c["rows"]:
            day = date.fromisoformat(r["day"])
            r["warnings"], reason = [], ""
            if not c["plate"]:
                reason = "Karta nemá ŠPZ, tankovanie sa nedá priradiť k vozidlu."
            elif not v:
                reason = f"Vozidlo {c['plate']} nie je v Knihe jázd."
            elif r["gross"] <= 0 or r["liters"] <= 0:
                reason = "Dobropis alebo oprava (záporná suma) – zapíšte ručne."
            elif not is_fuel(r["product"]):
                reason = f"Nie je to palivo ({r['product'] or 'neznámy produkt'})."
            elif day < v.start_date:
                reason = f"Dátum je pred začiatkom evidencie vozidla ({v.start_date:%d. %m. %Y})."
            elif (closed.get(v.id) and month_of(day) <= closed[v.id]) or (v.imported_lock and day <= v.imported_lock):
                reason = "Obdobie vozidla je uzavreté."
            elif r["liters"] > v.capacity:
                reason = f"Objem {r['liters']} l presahuje nádrž vozidla ({v.capacity} l)."
            else:
                key = (v.id, r["voucher"])
                duplicate = key in seen or db.scalar(select(Fuel.id).where(
                    Fuel.vehicle_id == v.id, Fuel.day == day,
                    (Fuel.note.contains(f"poukážka {r['voucher']}")) | ((Fuel.liters == r["liters"]) & (Fuel.gross == r["gross"]))).limit(1))
                seen.add(key)
                if duplicate:
                    reason = "Rovnaké tankovanie je už v knihe (z tejto faktúry alebo z bločku)."
            if v and not reason:
                want, got = fuel_kind(v.fuel_type), fuel_kind(r["product"])
                if want and got and want != got:
                    r["warnings"].append(f"Palivo vozidla je {v.fuel_type}, natankované bolo {r['product']}.")
            r["write"] = not reason
            r["reason"] = reason
            write += r["write"]
            skip += not r["write"]
    parsed.update(already=bool(existing), write=write, skip=skip,
                  imported_at=existing.created_at.isoformat() if existing else None)
    return parsed


def note(parsed: dict, row: dict) -> str:
    return f"{parsed['supplier']} {parsed['number']}, poukážka {row['voucher']}"

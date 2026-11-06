import os
import hashlib
import json
from uuid import uuid4
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo
from zipfile import BadZipFile

from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from openpyxl.utils.exceptions import InvalidFileException
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import IntegrityError

from . import assistant, distances, exports, importer, planner
from .auth import COOKIE, DUMMY, administrator, check_password, current_user, db_session, digest, hash_password, login
from .db import Access, Audit, Batch, Closing, Destination, Fuel, Holiday, Login, MonthlyDraft, Trip, User, Vehicle, now
from .ledger import events, ledger, month_of, months, next_month, report, rounded
from .schemas import Credentials, DestinationIn, FuelIn, TripIn, UserIn, VehicleIn, MonthlyPlanIn, MonthlyApproval

app = FastAPI(title="Kniha jázd", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(assistant.router)
Db = Depends(db_session)
Member = Depends(current_user)
Admin = Depends(administrator)

def json_value(data):
    return jsonable_encoder(data, custom_encoder={Decimal: str})

@app.middleware("http")
async def protect_writes(request, call_next):
    from urllib.parse import urlsplit
    origin=request.headers.get("origin")
    if request.method in {"POST","PUT","DELETE","PATCH"} and (request.headers.get("sec-fetch-site") in {"cross-site","same-site"} or (origin and urlsplit(origin).netloc != request.headers.get("host"))):
        return JSONResponse({"detail":"Požiadavka musí pochádzať z aplikácie."},status_code=403)
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    return response

@app.exception_handler(RequestValidationError)
async def invalid_request(request, error):
    messages = [item["msg"].removeprefix("Value error, ") for item in error.errors() if item["type"] == "value_error"]
    return JSONResponse({"detail":messages[0] if messages else "Skontrolujte povinné polia, formát dátumu a rozsah čísel."}, status_code=422)

@app.exception_handler(IntegrityError)
async def conflicting_record(request, error):
    return JSONResponse({"detail":"Záznam už existuje alebo sa nedá uložiť pre konflikt údajov."}, status_code=409)

def fail(message, code=422):
    raise HTTPException(code, message)

def today():
    return datetime.now(ZoneInfo("Europe/Bratislava")).date()

def audit(db,user,action,vehicle=None,**data):
    db.add(Audit(user_id=user.id, vehicle_id=vehicle, action=action, data=json_value(data)))

def serialized(row):
    return {c.name:getattr(row,c.name) for c in row.__table__.columns}

def vehicle_for(db,user,identifier,lock=False):
    query = select(Vehicle).where(Vehicle.id == identifier)
    if lock:
        query = query.with_for_update()
    vehicle = db.scalar(query)
    if not vehicle or (not user.admin and not db.get(Access,(user.id,identifier))):
        fail("Vozidlo nie je dostupné.",404)
    return vehicle

def check_open(db,vehicle,day):
    closed = db.scalar(select(func.max(Closing.month)).where(Closing.vehicle_id == vehicle.id))
    if (closed and month_of(day) <= closed) or (vehicle.imported_lock and day <= vehicle.imported_lock):
        fail("Obdobie je uzavreté. Záznam je chránený.",409)

def check_day(db,vehicle,day):
    check_open(db,vehicle,day)
    if day < vehicle.start_date:
        fail("Dátum predchádza začiatku evidencie vozidla.")

def row_for(db,model,vehicle,identifier):
    row = db.get(model,identifier)
    if not row or row.vehicle_id != vehicle.id:
        fail("Záznam sa nenašiel.",404)
    return row

@app.get("/api/health")
def health(db=Db):
    db.execute(text("SELECT 1"))
    return {"stav":"ok"}

@app.post("/api/login")
def sign_in(data: Credentials, response: Response, request: Request, db=Db):
    raise HTTPException(410, 'Použite spoločné prihlásenie na hlavnej stránke KBM.')
    user = db.scalar(select(User).where(User.username == data.username.lower()))
    valid = check_password(data.password,user.password if user else DUMMY)
    if not valid or not user or not user.active:
        fail("Nesprávne meno alebo heslo.",401)
    old = request.cookies.get(COOKIE)
    if old:
        db.execute(delete(Login).where(Login.digest == digest(old)))
    db.execute(delete(Login).where(Login.expires <= now()))
    login(db,response,user)
    return {"username":user.username,"admin":user.admin}

@app.post("/api/logout")
def sign_out(request: Request, response: Response, db=Db):
    token = request.cookies.get(COOKIE)
    if token:
        db.execute(delete(Login).where(Login.digest == digest(token)))
        db.commit()
    response.delete_cookie(COOKIE,path="/api")
    return {"ok":True}

@app.get("/api/me")
def me(user=Member):
    return {"id":user.id,"username":user.username,"admin":user.admin}

@app.get("/api/vehicles")
def vehicles(db=Db,user=Member):
    query = select(Vehicle).order_by(Vehicle.registration)
    if not user.admin:
        query = query.join(Access).where(Access.user_id == user.id)
    return json_value([serialized(v) for v in db.scalars(query)])

@app.post("/api/vehicles")
@app.put("/api/vehicles/{identifier}")
def save_vehicle(data: VehicleIn, identifier: int | None = None, db=Db,user=Admin):
    values = data.model_dump()
    values["registration"] = values["registration"].upper()
    if identifier is None:
        vehicle = Vehicle(**values)
        db.add(vehicle)
        db.flush()
        before = None
    else:
        vehicle = vehicle_for(db,user,identifier,True)
        before = serialized(vehicle)
        if db.scalar(select(Closing).where(Closing.vehicle_id == identifier).limit(1)):
            fail("Nastavenia vozidla možno meniť po otvorení jeho uzávierok.",409)
        if vehicle.imported_lock and vehicle.imported_lock >= vehicle.start_date:
            fail("Najprv otvorte uzávierku prenesenú z Excelu.",409)
        rows = events(db,vehicle)
        if rows and (values["initial_odo"] != vehicle.initial_odo or values["start_date"] > rows[0].day):
            fail("Existujúce záznamy by prestali nadväzovať na začiatok evidencie.")
        for key,value in values.items():
            setattr(vehicle,key,value)
    audit(db,user,"Vozidlo uložené",vehicle.id,before=before,after=serialized(vehicle))
    db.commit()
    return json_value(serialized(vehicle))

@app.get("/api/vehicles/{identifier}/records")
def records(identifier: int, year: int=Query(ge=1900,le=9998), month: int=Query(ge=1,le=12),db=Db,user=Member):
    vehicle = vehicle_for(db,user,identifier)
    return json_value(months(db,vehicle,year)[month-1])

@app.get("/api/vehicles/{identifier}/odometer")
def odometer(identifier: int, day: date, db=Db,user=Member):
    vehicle = vehicle_for(db,user,identifier)
    rows = [r for r in events(db,vehicle) if isinstance(r,Trip) and r.day <= day]
    return {"odo":rows[-1].end_odo if rows else vehicle.initial_odo,
            "vat":23 if day.year >= 2025 else 20}

def save_trip_row(db,user,vehicle,data,identifier):
    values = data.model_dump()
    row = row_for(db,Trip,vehicle,identifier) if identifier else None
    before = serialized(row) if row else None
    if row:
        check_open(db,vehicle,row.day)
    check_day(db,vehicle,data.day)
    trips = [r for r in events(db,vehicle) if isinstance(r,Trip) and r.id != identifier]
    prior = [r for r in trips if (r.day,r.end_odo) < (data.day,data.end_odo)]
    subsequent = [r for r in trips if (r.day,r.end_odo) >= (data.day,data.end_odo)]
    start = prior[-1].end_odo if prior else vehicle.initial_odo
    if data.start_odo is not None and data.start_odo != start:
        fail(f"Počiatočný tachometer musí nadväzovať na {start} km.")
    if data.end_odo <= start or (subsequent and data.end_odo >= subsequent[0].end_odo):
        fail("Koncový tachometer musí byť medzi predchádzajúcim a nasledujúcim stavom.")
    values.update(start_odo=start,rate=data.rate or (vehicle.city_rate if data.category == "mesto" else vehicle.outside_rate))
    if row:
        for key,value in values.items():
            setattr(row,key,value)
    else:
        row = Trip(vehicle_id=vehicle.id,**values)
        db.add(row)
    db.flush()
    recalculate_trips(db,vehicle)
    audit(db,user,"Jazda uložená",vehicle.id,before=before,after=serialized(row))
    return row

def recalculate_trips(db,vehicle):
    previous = vehicle.initial_odo
    for trip in [r for r in events(db,vehicle) if isinstance(r,Trip)]:
        if trip.end_odo <= previous:
            fail("Zmena narúša poradie tachometra v nasledujúcich jazdách.")
        if trip.start_odo != previous:
            check_open(db,vehicle,trip.day)
            trip.start_odo = previous
        previous = trip.end_odo

@app.post("/api/vehicles/{vehicle_id}/trips")
@app.put("/api/vehicles/{vehicle_id}/trips/{identifier}")
def save_trip(vehicle_id: int,data: TripIn,identifier: int | None=None,db=Db,user=Member):
    vehicle = vehicle_for(db,user,vehicle_id,True)
    row = save_trip_row(db,user,vehicle,data,identifier)
    db.commit()
    return {"id":row.id}

@app.post("/api/vehicles/{vehicle_id}/fuel")
@app.put("/api/vehicles/{vehicle_id}/fuel/{identifier}")
def save_fuel(vehicle_id: int,data: FuelIn,identifier: int | None=None,db=Db,user=Member):
    vehicle = vehicle_for(db,user,vehicle_id,True)
    row = row_for(db,Fuel,vehicle,identifier) if identifier else None
    before = serialized(row) if row else None
    if row:
        check_open(db,vehicle,row.day)
    check_day(db,vehicle,data.day)
    if (data.odo is not None and data.odo < vehicle.initial_odo) or (data.measured is not None and data.measured > vehicle.capacity):
        fail("Tachometer alebo stav nádrže je mimo rozsahu.")
    if data.liters > vehicle.capacity:
        fail("Objem tankovania presahuje kapacitu nádrže.")
    values = data.model_dump(exclude={"unit_price","clock"})
    values["clock"] = data.clock.isoformat() if data.clock else ""
    values["gross"] = rounded(data.gross if data.gross is not None else data.unit_price * data.liters)
    if values["gross"] <= 0:
        fail("Celková suma musí byť aspoň 0,01 €.")
    duplicate = db.scalar(select(Fuel).where(Fuel.vehicle_id == vehicle_id,Fuel.day == data.day,
        Fuel.odo.is_(None) if data.odo is None else Fuel.odo == data.odo,Fuel.liters == data.liters,Fuel.gross == values["gross"],
        Fuel.clock == values["clock"],Fuel.station == values["station"],Fuel.id != (identifier or 0)).limit(1))
    if duplicate:
        fail("Rovnaké tankovanie je už uložené.",409)
    if row:
        for key,value in values.items():
            setattr(row,key,value)
    else:
        row = Fuel(vehicle_id=vehicle.id,**values)
        db.add(row)
    db.flush()
    audit(db,user,"Tankovanie uložené",vehicle.id,before=before,after=serialized(row))
    db.commit()
    return {"id":row.id}

@app.delete("/api/vehicles/{vehicle_id}/{kind}/{identifier}")
def remove(vehicle_id: int,kind: str,identifier: int,db=Db,user=Member):
    models = {"trips":Trip,"fuel":Fuel,"destinations":Destination}
    model = models.get(kind)
    if model is None:
        fail("Typ záznamu sa nenašiel.",404)
    vehicle = vehicle_for(db,user,vehicle_id,True)
    row = row_for(db,model,vehicle,identifier)
    if model is not Destination:
        check_open(db,vehicle,row.day)
    audit(db,user,{"trips":"Jazda odstránená","fuel":"Tankovanie odstránené","destinations":"Cieľ odstránený"}[kind],
          vehicle.id,before=serialized(row))
    db.delete(row)
    if model is Trip:
        db.flush()
        recalculate_trips(db,vehicle)
    db.commit()
    return {"ok":True}

@app.get("/api/vehicles/{vehicle_id}/destinations")
def destinations(vehicle_id: int,db=Db,user=Member):
    vehicle_for(db,user,vehicle_id)
    return [serialized(r) for r in db.scalars(select(Destination).where(
        Destination.vehicle_id == vehicle_id).order_by(Destination.name))]

@app.post("/api/vehicles/{vehicle_id}/destinations")
@app.put("/api/vehicles/{vehicle_id}/destinations/{identifier}")
def save_destination(vehicle_id: int,data: DestinationIn,identifier: int | None=None,db=Db,user=Member):
    vehicle = vehicle_for(db,user,vehicle_id,True)
    values = data.model_dump()
    if identifier is None:
        row = Destination(vehicle_id=vehicle.id,**values)
        db.add(row)
    else:
        row = row_for(db,Destination,vehicle,identifier)
        for key,value in values.items():
            setattr(row,key,value)
    db.commit()
    return serialized(row)

@app.get("/api/vehicles/{identifier}/reports")
def reports(identifier: int,year: int=Query(ge=1900,le=9998),db=Db,user=Member):
    vehicle = vehicle_for(db,user,identifier)
    return json_value(months(db,vehicle,year))

@app.post("/api/vehicles/{identifier}/close")
def close(identifier: int,month: date,db=Db,user=Admin):
    vehicle = vehicle_for(db,user,identifier,True)
    month = month_of(month)
    check_open(db,vehicle,next_month(month)-timedelta(days=1))  # a month locked only partly can still be closed
    if month >= month_of(today()) or next_month(month) <= vehicle.start_date:
        fail("Uzavrieť možno iba skončený mesiac od začiatku evidencie.")
    last = db.scalar(select(func.max(Closing.month)).where(Closing.vehicle_id == identifier))
    # A lock imported from Excel may fall mid-month; that month stays open after the lock day and can be closed.
    lock = vehicle.imported_lock
    lock_next = (next_month(month_of(lock)) if lock and next_month(lock) == lock + timedelta(days=1) else month_of(lock)) if lock else None
    starts = [value for value in (next_month(last) if last else None, lock_next) if value]
    current = max(month_of(vehicle.start_date), *starts) if starts else month_of(vehicle.start_date)
    rows = ledger(db,vehicle)
    while current <= month:
        snapshot = json_value(report(vehicle,rows,current))
        db.add(Closing(vehicle_id=identifier,month=current,snapshot=snapshot))
        audit(db,user,"Mesiac uzavretý",identifier,month=current,snapshot=snapshot)
        current = next_month(current)
    db.commit()
    return {"ok":True}

@app.post("/api/vehicles/{identifier}/reopen")
def reopen(identifier: int,month: date,db=Db,user=Admin):
    vehicle = vehicle_for(db,user,identifier,True)
    month = month_of(month)
    latest = db.scalar(select(func.max(Closing.month)).where(Closing.vehicle_id == identifier))
    if latest and month == latest:
        db.delete(db.get(Closing,(identifier,month)))
    elif not latest and vehicle.imported_lock and month_of(vehicle.imported_lock) == month:
        vehicle.imported_lock = None
    else:
        fail("Najprv otvorte najnovšiu uzávierku.",409)
    audit(db,user,"Obdobie otvorené",identifier,month=month)
    db.commit()
    return {"ok":True}

def annual(reports):
    first,last = reports[0],reports[-1]
    totals = {key:sum(Decimal(str(row[key])) for row in reports) for key in exports.REPORT
              if key not in {"month","opening_odo","closing_odo","opening_liters","closing_liters",
                  "opening_net","closing_net","average","gross_per_liter"}}
    totals.update(month="Spolu",opening_odo=first["opening_odo"],closing_odo=last["closing_odo"],
                  opening_liters=first["opening_liters"],closing_liters=last["closing_liters"],
                  opening_net=first["opening_net"],closing_net=last["closing_net"])
    totals["average"] = rounded(totals["consumption"]*100/totals["distance"]) if totals["distance"] else 0
    totals["gross_per_liter"] = rounded(totals["gross"]/totals["purchased"],"0.0001") if totals["purchased"] else 0
    return totals

@app.get("/api/vehicles/{identifier}/export")
def export(identifier: int,year: int=Query(ge=1900,le=9998),month: int | None=Query(None,ge=1,le=12),
           format: str="xlsx",kind: str="report",db=Db,user=Member):
    vehicle = vehicle_for(db,user,identifier)
    results = months(db,vehicle,year)
    selected = [results[month-1]] if month else results
    rows = [row for value in selected for row in value["rows"]]
    trips = [row for row in rows if row["type"] == "trip"]
    purchases = [row for row in rows if row["type"] == "fuel"]
    context = dict(vehicle=vehicle, year=year, month=month, months=selected, trips=trips, fuels=purchases,
                   report=selected[0] if month else annual(selected), author=getattr(user, "display_name", user.username),
                   day=today(), place=distances.HOME)
    period = f"{year}-{month:02}" if month else str(year)
    if format == "pdf" and kind == "report":
        data, mime = exports.pdf(context),"application/pdf"
    elif format == "xlsx" and kind in ("report", "trips", "fuel"):
        data,mime = exports.xlsx(context, kind),"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    else:
        fail("Nepodporovaný formát exportu.")
    kind_name = {"report": "vykaz", "trips": "jazdy", "fuel": "tankovanie"}[kind]
    plate = "".join(char for char in vehicle.registration if char.isascii() and (char.isalnum() or char in "-_"))
    filename = f"{kind_name}-{plate or 'vozidlo'}-{period}.{format}"
    return Response(data,media_type=mime,headers={"Content-Disposition":f'attachment; filename="{filename}"'})

@app.get("/api/users")
def users(db=Db,user=Admin):
    from .auth import sync_directory
    people = sync_directory(db)   # iba účty s prístupom ku Knihe jázd podľa portálu
    access = list(db.scalars(select(Access)))
    return [{"id":r.id,"username":r.username,"name":(people or {}).get(r.username,{}).get("name",r.username),"admin":r.admin,"active":r.active,
             "vehicle_ids":[a.vehicle_id for a in access if a.user_id == r.id]}
            for r in db.scalars(select(User).order_by(User.username)) if people is None or r.username in people]

@app.post("/api/users")
@app.put("/api/users/{identifier}")
def save_user(data: UserIn,identifier: int | None=None,db=Db,user=Admin):
    if identifier is None: fail("Účty sa pridávajú v portáli KBM (Správa používateľov).",409)
    existing=db.get(User,identifier)
    if existing and (data.username != existing.username or data.admin != existing.admin or data.password): fail("Identitu, rolu a heslo spravuje spoločný portál.",409)
    if identifier == user.id and (not data.admin or not data.active):
        fail("Vlastný účet správcu musí zostať aktívny.")
    access = set(data.vehicle_ids)
    if access and set(db.scalars(select(Vehicle.id).where(Vehicle.id.in_(access)))) != access:
        fail("Zvolené vozidlo neexistuje.")
    values = data.model_dump(exclude={"password","vehicle_ids"})
    values["username"] = data.username.lower()
    if identifier is None:
        if not data.password:
            fail("Zadajte heslo s aspoň 12 znakmi.")
        row = User(password=hash_password(data.password),**values)
        db.add(row)
        db.flush()
    else:
        row = db.get(User,identifier)
        if not row:
            fail("Používateľ sa nenašiel.",404)
        for key,value in values.items():
            setattr(row,key,value)
        if data.password:
            row.password = hash_password(data.password)
        if data.password or not data.active:
            db.execute(delete(Login).where(Login.user_id == row.id))
        db.execute(delete(Access).where(Access.user_id == row.id))
    db.add_all(Access(user_id=row.id,vehicle_id=vehicle_id) for vehicle_id in access)
    audit(db,user,"Používateľ uložený",account=row.id,**values,vehicle_ids=sorted(access))
    db.commit()
    return {"id":row.id}

@app.get("/api/audit")
def audit_log(db=Db,user=Admin):
    return json_value([serialized(r) for r in db.scalars(select(Audit).order_by(Audit.id.desc()).limit(200))])

@app.post("/api/import")
def workbook_import(file: UploadFile=File(),apply: bool=False,expected_digest: str | None=None,db=Db,user=Admin):
    if not (file.filename or "").lower().endswith(".xlsm"):
        fail("Vyberte súbor XLSM.")
    content = file.file.read(8_000_001)
    if len(content) > 8_000_000:
        fail("Súbor presahuje 8 MB.")
    try:
        parsed = importer.parse(content)
    except (ValueError,TypeError,KeyError,BadZipFile,InvalidFileException,ArithmeticError,OverflowError):
        fail("Zošit sa nedá načítať. Skontrolujte pôvodnú šablónu a uložené nastavenia.")
    fingerprint = parsed["digest"]
    existing = db.get(Batch,fingerprint)
    duplicate = db.scalar(select(Vehicle.id).where(Vehicle.registration == parsed["vehicle"]["registration"]))
    if apply:
        if expected_digest != fingerprint:
            fail("Súbor sa zmenil. Znova vytvorte náhľad.",409)
        if existing or duplicate:
            fail("Zošit alebo vozidlo už bolo importované. Existujúce dáta zostali zachované.",409)
        vehicle = Vehicle(**parsed["vehicle"])
        db.add(vehicle)
        db.flush()
        for model,key in ((Trip,"trips"),(Fuel,"purchases"),(Destination,"destinations")):
            db.add_all(model(vehicle_id=vehicle.id,**row) for row in parsed[key])
        db.add_all(Holiday(vehicle_id=vehicle.id,day=day) for day in parsed["holidays"])
        db.add(Batch(digest=fingerprint,vehicle_id=vehicle.id,warnings=parsed["warnings"]))
        audit(db,user,"Excel importovaný",vehicle.id,digest=fingerprint,warnings=parsed["warnings"])
        db.commit()
        return {"vehicle_id":vehicle.id}
    return json_value({**parsed,"duplicate":bool(existing or duplicate)})

@app.post("/api/vehicles/{identifier}/monthly-drafts")
def propose_month(identifier: int, data: MonthlyPlanIn, db=Db, user=Member):
    vehicle = vehicle_for(db, user, identifier, True)
    try:
        payload = planner.build_plan(db, vehicle, data, today())
    except planner.PlanError as error:
        fail(str(error), 409)
    payload['input'] = json_value(data.model_dump())
    draft = MonthlyDraft(id=str(uuid4()), vehicle_id=identifier, user_id=user.id, month=data.month,
                         fingerprint=planner.fingerprint(db, vehicle), payload=payload)
    db.add(draft)
    db.commit()
    return {"draft_id": draft.id, **payload}

@app.post("/api/vehicles/{identifier}/monthly-drafts/{draft_id}/approve")
def approve_month(identifier: int, draft_id: str, data: MonthlyApproval, db=Db, user=Member):
    vehicle = vehicle_for(db, user, identifier, True)
    draft = db.scalar(select(MonthlyDraft).where(MonthlyDraft.id == draft_id,
        MonthlyDraft.vehicle_id == identifier).with_for_update())
    if not draft or (draft.user_id != user.id and not user.admin):
        fail("Návrh sa nenašiel.", 404)
    if (draft.payload or {}).get("source") == "assistant":
        fail("Tento návrh bol uložený cez mesačného sprievodcu a nedá sa schváliť znova.", 409)
    reviewed = json_value([row.model_dump() for row in data.rows])
    approval_hash = hashlib.sha256(json.dumps(reviewed, sort_keys=True).encode()).hexdigest()
    if draft.result:
        if draft.result.get('approval_hash') != approval_hash:
            fail("Návrh už bol uložený s inými údajmi.", 409)
        return draft.result
    if draft.fingerprint != planner.fingerprint(db, vehicle):
        fail("Evidencia sa od vytvorenia návrhu zmenila. Vytvorte a skontrolujte nový návrh.", 409)
    try:
        planner.validate_review(db, vehicle, draft, data.rows, today())
    except planner.PlanError as error:
        fail(str(error), 409)
    records = []
    for values in data.rows:
        fields = values.model_dump()
        fields['rate'] = values.rate or (vehicle.city_rate if values.category == 'mesto' else vehicle.outside_rate)
        trip = Trip(vehicle_id=identifier, estimated=True, draft_id=draft.id, **fields)
        db.add(trip)
        records.append(trip)
    db.flush()
    draft.result = dict(trip_ids=[row.id for row in records], count=len(records), approval_hash=approval_hash)
    audit(db, user, "Mesačný návrh schválený", identifier, draft_id=draft.id,
          input=draft.payload['input'], proposed=draft.payload['rows'], reviewed=reviewed,
          warnings=draft.payload['warnings'], trip_ids=draft.result['trip_ids'])
    db.commit()
    return draft.result

@app.post("/api/demo")
def seed_demo(db=Db, user=Admin):
    # Serialize repeated clicks in PostgreSQL, while keeping all changes in one transaction.
    db.scalar(select(User).where(User.id == user.id).with_for_update())
    registration = 'DEMO-KJ-01'
    vehicle = db.scalar(select(Vehicle).where(Vehicle.registration == registration))
    if vehicle:
        marker = db.scalar(select(Audit.id).where(Audit.vehicle_id == vehicle.id,
                           Audit.action == 'Demo dáta vytvorené').limit(1))
        if not marker:
            fail("EČV DEMO-KJ-01 už existuje v inej evidencii.", 409)
        target = next_month(vehicle.start_date)
        return dict(vehicle_id=vehicle.id, year=target.year, month=target.month,
                    start_odo=12240, end_odo=13280, existing=True)
    month = month_of(today())
    previous = month_of(month - timedelta(days=1))
    history = month_of(previous - timedelta(days=1))
    vehicle = Vehicle(name='DEMO · Mesačná evidencia', registration=registration,
        responsible='Testovacie údaje', kind='osobné', fuel_type='benzín', start_date=history,
        initial_odo=12000, capacity=60, initial_liters=50, initial_net=Decimal('65.00'),
        city_rate=Decimal('8'), outside_rate=Decimal('6'), city_limit=60)
    db.add(vehicle)
    db.flush()
    for name, origin, km in [('Košice','Spišská Nová Ves',45),('Prešov','Spišská Nová Ves',40),
                             ('Poprad','Spišská Nová Ves',25),('Mesto','Spišská Nová Ves',10)]:
        db.add(Destination(vehicle_id=vehicle.id, name=name, origin=origin, distance=km,
                           note='DEMO · Orientačná vzdialenosť na testovanie.'))
    odo = 12000
    for day, destination, km, category in [(3,'Košice',90,'mimo mesto'),
            (10,'Mesto',20,'mesto'),(17,'Prešov',80,'mimo mesto'),(24,'Poprad',50,'mimo mesto')]:
        db.add(Trip(vehicle_id=vehicle.id, day=history.replace(day=day), start_odo=odo, end_odo=odo+km,
                    origin='Spišská Nová Ves', destination=destination, category=category,
                    purpose='DEMO · Návšteva zákazníka', note='DEMO · Testovacia jazda',
                    rate=vehicle.city_rate if category == 'mesto' else vehicle.outside_rate))
        odo += km
    for day, reading, liters, gross in [(12,12720,'30','49.20'),(26,13280,'35','57.75')]:
        db.add(Fuel(vehicle_id=vehicle.id, day=previous.replace(day=day), odo=reading,
                    liters=Decimal(liters), gross=Decimal(gross), vat=Decimal('23'),
                    station='DEMO čerpacia stanica', clock='17:00:00', note='DEMO · Fiktívny doklad'))
    audit(db, user, 'Demo dáta vytvorené', vehicle.id, history=history, proposal_month=previous)
    db.commit()
    return dict(vehicle_id=vehicle.id, year=previous.year, month=previous.month,
                start_odo=12240, end_odo=13280, existing=False)

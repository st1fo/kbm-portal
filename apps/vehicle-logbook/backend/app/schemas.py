from datetime import date, time
from decimal import Decimal
from typing import Annotated, Literal
from pydantic import BaseModel, Field, StringConstraints, model_validator

Short = Annotated[str, StringConstraints(strip_whitespace=True, max_length=150)]
Notes = Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]
Odo = Annotated[int, Field(ge=0, le=10_000_000)]
Amount = Annotated[Decimal, Field(ge=0, le=10_000_000, max_digits=20, decimal_places=10)]
Positive = Annotated[Decimal, Field(gt=0, le=100_000, max_digits=20, decimal_places=10)]

class VehicleIn(BaseModel):
    name: Annotated[Short, Field(min_length=1)]
    registration: Annotated[Short, Field(min_length=1, max_length=30)]
    fuel_type: Annotated[Short, Field(max_length=60)] = ""
    kind: Annotated[Short, Field(max_length=60)] = ""
    responsible: Annotated[Short, Field(max_length=120)] = ""
    start_date: date
    initial_odo: Odo
    capacity: Positive
    initial_liters: Amount = Decimal(0)
    initial_net: Amount = Decimal(0)
    city_rate: Positive
    outside_rate: Positive
    city_limit: Odo = 60

    @model_validator(mode="after")
    def check_balance(self):
        if self.initial_liters > self.capacity or (self.initial_liters == 0 and self.initial_net > 0):
            raise ValueError("Skontrolujte počiatočný stav nádrže a jeho hodnotu.")
        return self

class VehicleDelete(BaseModel):
    registration: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]

class TripIn(BaseModel):
    day: date
    start_odo: Odo | None = None
    end_odo: Odo
    origin: Short = ""
    destination: Short = ""
    purpose: Short = ""
    category: Literal["mesto", "mimo mesto"]
    note: Notes = ""
    rate: Positive | None = None

class FuelIn(BaseModel):
    day: date
    odo: Odo | None = None
    liters: Positive
    gross: Positive | None = None
    unit_price: Positive | None = None
    vat: Annotated[Amount, Field(le=100)]
    station: Short = ""
    clock: time | None = None
    note: Notes = ""
    measured: Amount | None = None

    @model_validator(mode="after")
    def require_price(self):
        if (self.gross is None) == (self.unit_price is None):
            raise ValueError("Zadajte buď celkovú sumu, alebo cenu za liter.")
        return self

class DestinationIn(BaseModel):
    name: Annotated[Short, Field(min_length=1)]
    origin: Short = ""
    distance: Annotated[int, Field(gt=0, le=100_000)]
    note: Notes = ""

class Credentials(BaseModel):
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)]

class UserIn(BaseModel):
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
    password: Annotated[str, StringConstraints(min_length=12, max_length=256)] | None = None
    admin: bool = False
    active: bool = True
    vehicle_ids: list[int] = []

class MonthlyRoute(BaseModel):
    destination: Annotated[Short, Field(min_length=1)]
    origin: Short = ""
    count: Annotated[int, Field(ge=1, le=60)] = 1
    approximate_km: Annotated[int, Field(ge=1, le=100_000)] | None = None
    category: Literal["mesto", "mimo mesto"] = "mimo mesto"
    purpose: Short = ""

class MonthlyPlanIn(BaseModel):
    month: date
    start_odo: Odo
    end_odo: Odo
    routes: Annotated[list[MonthlyRoute], Field(min_length=1, max_length=30)]
    note: Notes = ""

    @model_validator(mode="after")
    def valid_interval(self):
        if self.month.day != 1 or not 1900 <= self.month.year <= 9998:
            raise ValueError("Vyberte platný mesiac.")
        if self.end_odo <= self.start_odo:
            raise ValueError("Koncový tachometer musí byť vyšší ako počiatočný.")
        if sum(route.count for route in self.routes) > 120:
            raise ValueError("Mesačný návrh môže obsahovať najviac 120 jázd.")
        return self

class MonthlyApproval(BaseModel):
    rows: Annotated[list[TripIn], Field(min_length=1, max_length=120)]
    reviewed: Literal[True]

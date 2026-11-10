from datetime import date
from pydantic import BaseModel, Field, field_validator, model_validator

class Employee(BaseModel):
    name: str = Field(min_length=2,max_length=150)
    email: str = Field(min_length=5,max_length=254,pattern=r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
    role: str = Field(default='employee', pattern='^(admin|employee)$')
    active: bool = True
    daily_hours: float = Field(default=8,gt=0,le=24)
    password: str | None = Field(default=None,min_length=10,max_length=128)
class TypeInput(BaseModel):
    name: str = Field(min_length=2,max_length=100)
    icon: str = Field(pattern='^(Building2|House|Palmtree|HeartPulse|Stethoscope|Plane|GraduationCap|CalendarOff|CalendarDays|CircleHelp)$')
    color: str = Field(pattern='^#[0-9a-fA-F]{6}$')
    description: str = Field(default='',max_length=500)
    requires_approval: bool = False
    active: bool = True
class Plan(BaseModel):
    user_id: int
    type_id: int
    start_date: date
    end_date: date
    start: str | None = Field(default=None,pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    end: str | None = Field(default=None,pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    note: str = Field(default='',max_length=2000)
    replace: bool = False
    hours: float | None = Field(default=None,gt=0,le=24)
    @model_validator(mode='after')
    def valid(self):
        if self.hours is not None:
            if self.start_date != self.end_date: raise ValueError('Absencia na časť dňa musí byť v jednom dni.')
            if abs(self.hours*4-round(self.hours*4))>1e-9: raise ValueError('Počet hodín zadajte po štvrťhodinách (napr. 1,5).')
        if self.end_date < self.start_date or (self.end_date-self.start_date).days > 366: raise ValueError('Neplatný rozsah dátumov (max. 367 dní).')
        if bool(self.start) != bool(self.end) or (self.start and self.end <= self.start): raise ValueError('Koniec musí byť po začiatku.')
        return self
class Review(BaseModel):
    status: str = Field(pattern='^(approved|rejected)$')
    reason: str = Field(default='',max_length=2000)
class SettingsInput(BaseModel):
    daily_hours: float = Field(gt=0,le=24)
    weekends: list[int]
    @field_validator('weekends')
    @classmethod
    def valid_weekends(cls,v):
        if any(x<0 or x>6 for x in v) or len(v)>6: raise ValueError('Dni víkendu: 0 až 6, aspoň jeden pracovný deň.')
        return sorted(set(v))
class HolidayInput(BaseModel):
    date: date
    name: str = Field(min_length=2,max_length=150)

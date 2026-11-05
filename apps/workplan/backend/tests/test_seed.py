import os
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import sessionmaker
from app.db import Base
from app.models import User, Attendance, AttendanceType, Holiday
from app import seed
from app.init_data import init_reference

def test_reference_and_demo_initialization(tmp_path,monkeypatch):
    engine=create_engine('sqlite:///'+str(tmp_path/'seed.db'))
    Base.metadata.create_all(engine)
    factory=sessionmaker(engine)
    monkeypatch.setattr(seed,'SessionLocal',factory)
    monkeypatch.setenv('DEMO_PASSWORD','Demo-test-only-2026')
    with factory() as db:
        init_reference(db);db.commit();init_reference(db);db.commit()
        assert db.scalar(select(func.count(AttendanceType.id)))==10
        assert db.get(Holiday,__import__('datetime').date(2026,11,17)) is None
    seed.seed();seed.seed()
    with factory() as db:
        assert db.scalar(select(func.count(User.id)))==4
        assert db.scalar(select(func.count(Attendance.id)))==84

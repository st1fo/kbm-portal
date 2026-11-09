"""Monthly chat wizard with a simulated Gemini client.

Run from apps/vehicle-logbook/backend:  python -m pytest tests/test_assistant.py -q
Portal SSO is replaced by a stub identity (cookie value = username); nothing calls the real Gemini API.
"""
import json
import os
import unittest
from datetime import date
from decimal import Decimal as D
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import io

from alembic import command
from openpyxl import load_workbook
from alembic.config import Config

sandbox = TemporaryDirectory()
os.environ.setdefault("DATABASE_URL", f"sqlite:///{sandbox.name}/assistant.sqlite")
os.environ["COOKIE_SECURE"] = "0"

from fastapi.testclient import TestClient  # noqa: E402

from app import gemini, portal_sso  # noqa: E402
from app.db import Fuel, Session, Trip  # noqa: E402
from app.main import app  # noqa: E402
from sqlalchemy import select  # noqa: E402

BASE = Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
PDF = b"%PDF-1.4\n%fictional receipt\n"


class FakeModels:
    """Returns queued JSON answers per schema, like `client.models.generate_content` would."""
    def __init__(self):
        self.queue = {"ReceiptExtraction": [], "ChatResult": []}
        self.calls = []
        self.error = None

    def generate_content(self, model, contents, config):
        self.calls.append((model, config.response_schema.__name__, contents))
        if self.error:
            raise self.error
        return SimpleNamespace(text=json.dumps(self.queue[config.response_schema.__name__].pop(0)))


FAKE = FakeModels()


def stub_identity(request):
    from fastapi import HTTPException
    slug = request.cookies.get("kbm_portal_session")
    if not slug:
        raise HTTPException(401, "Prihláste sa do portálu KBM.")
    return {"slug": slug, "admin": slug == "maros", "name": "Maroš Sokolák" if slug == "maros" else slug}


class ApiError(Exception):
    def __init__(self, code):
        super().__init__(f"{code} error")
        self.code = code


class AssistantFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = Config(str(BASE / "alembic.ini"))
        command.upgrade(config, "head")
        command.check(config)  # ORM models and migrations must match
        portal_sso.portal_identity = stub_identity
        gemini.client_factory = lambda: SimpleNamespace(models=FAKE)
        os.environ["GEMINI_API_KEY"] = "test-key"
        cls.admin = TestClient(app, cookies={"kbm_portal_session": "maros"})
        demo = cls.admin.post("/api/demo").json()
        cls.vehicle = demo["vehicle_id"]
        cls.month = date(demo["year"], demo["month"], 1)

    def setUp(self):
        FAKE.error = None

    def ok(self, response, status=200):
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def day(self, number):
        return self.month.replace(day=number).isoformat()

    def open(self, client=None):
        return self.ok((client or self.admin).post(f"/api/vehicles/{self.vehicle}/assistant",
                                                    json={"month": self.month.isoformat()}))

    def receipt(self, day=None, liters=None, gross=None, odo=None, unreadable=(), station="DEMO pumpa (fiktívna)"):
        return {"day": day, "station": station, "liters": liters, "gross": gross, "currency": "EUR", "vat": 23,
                "odo": odo, "unreadable": list(unreadable)}

    def chat(self, sid, text, reply="Rozumiem.", **result):
        FAKE.queue["ChatResult"].append({"reply": reply, "operations": [], "create_plan": False, **result})
        return self.ok(self.admin.post(f"/api/assistant/{sid}/messages", json={"text": text}))

    def test_full_month_flow(self):
        draft = self.open()
        sid = draft["id"]
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["expected_start"], 12240)
        self.assertTrue(draft["assistant"]["configured"])

        # Upload limits and file type checks.
        for name, content in [("blocek1.png", PNG), ("blocek2.pdf", PDF), ("blocek3.png", PNG + b"3")]:
            draft = self.ok(self.admin.post(f"/api/assistant/{sid}/files", files={"file": (name, content)}))
        self.ok(self.admin.post(f"/api/assistant/{sid}/files", files={"file": ("again.png", PNG)}), 409)
        self.ok(self.admin.post(f"/api/assistant/{sid}/files", files={"file": ("x.txt", b"hello")}), 422)
        self.assertEqual([f["status"] for f in draft["files"]], ["pending"] * 3)
        self.assertFalse(draft["can_confirm"])

        # Gemini extracts receipts: one is complete, one has an unreadable total, one duplicates the logbook.
        FAKE.queue["ReceiptExtraction"] += [
            {"is_receipt": True, "receipts": [self.receipt(self.day(5), 31.4, 51.81, 12410)]},
            {"is_receipt": True, "receipts": [self.receipt(self.day(20), 40, None, None, ["gross"])]},
            {"is_receipt": True, "receipts": [self.receipt(self.day(12), 30, 49.2, 12720)]},
        ]
        draft = self.ok(self.admin.post(f"/api/assistant/{sid}/extract"))
        receipts = {r["id"]: r for r in draft["receipts"]}
        self.assertEqual(set(receipts), {"B1", "B2", "B3"})
        self.assertIn("gross", receipts["B2"]["missing"])
        self.assertEqual(receipts["B2"]["unreadable"], ["gross"])
        self.assertIsNone(receipts["B2"]["odo"])
        self.assertIn("evidencii", receipts["B3"]["duplicate"])
        self.assertFalse(draft["can_confirm"])
        self.assertEqual(set(draft["highlight"]), {"B1", "B2", "B3"})
        self.ok(self.admin.post(f"/api/assistant/{sid}/confirm-receipts"), 422)
        # without a description of the month there is nothing to plan yet; the assistant asks for it
        planned = self.ok(self.admin.post(f"/api/assistant/{sid}/plan", json={}))
        self.assertEqual(planned["trips"], [])
        self.assertIn("Kde ste", planned["messages"][-1]["text"])

        # Manual correction in the table, removal of the duplicate.
        draft = self.ok(self.admin.post(f"/api/assistant/{sid}/operations", json={"operations": [
            {"action": "update_receipt", "row": "B2", "gross": "64.40"},
            {"action": "delete_receipt", "row": "B3"}]}))
        self.assertTrue(draft["can_confirm"])
        self.assertEqual(len(draft["files"]), 2)

        # The user describes the month: the schedule for the whole month is created right away (no confirm step).
        draft = self.chat(sid, "Na začiatku mesiaca som mal 12 240 km, na konci 13 280 km. Asi trikrát som bol "
                          "v Košiciach a dvakrát v Poprade.", start_odo=12240, end_odo=13280, create_plan=True,
                          routes=[{"destination": "Košice", "count": 3, "approximate_km": 200, "category": "mimo mesto", "days": []},
                                  {"destination": "Poprad", "count": 2, "approximate_km": 110, "category": "mimo mesto", "days": []}])
        self.assertEqual((draft["start_odo"], draft["end_odo"], draft["distance"]), (12240, 13280, 1040))
        self.assertEqual(len(draft["trips"]), 5)
        self.assertIn("Pripravil som návrh 5 jázd", draft["messages"][-1]["text"])
        # The base is Žilina: the demo catalogue (from Spišská Nová Ves) does not apply, the Žilina table does.
        self.assertEqual(draft["origin"], "Žilina")
        self.assertEqual([r["approximate_km"] for r in draft["routes"]], [490, 280])
        # Gemini received the draft as data, never as instructions.
        sent = FAKE.calls[-1][2][-1].parts[0].text
        self.assertTrue(sent.startswith("DATA"))

        draft = self.ok(self.admin.post(f"/api/assistant/{sid}/confirm-receipts"))
        self.assertTrue(draft["receipts_confirmed"] and draft["can_plan"])
        draft = self.ok(self.admin.post(f"/api/assistant/{sid}/plan", json={"rebuild": True}))
        self.assert_schedule(draft)
        self.assertEqual(len(draft["trips"]), 5)
        # Confirmed receipts refuel the draft tank; trips consume their norm (demo: 60 l tank, 6 l/100 km).
        tank = draft["tank"]
        self.assertTrue(tank["planned"])
        self.assertEqual(tank["purchased"], "136.4")  # B1 31.4 + B2 40 + saved demo fuels 30 + 35
        self.assertEqual(tank["consumed"], "62.4")    # 1 040 km × 6 l/100 km
        self.assertEqual(D(tank["closing"]), D(tank["opening"]) + D(tank["purchased"]) - D(tank["consumed"]))
        self.assertEqual(set(tank["rows"]) >= {"B1", "B2", "J1"}, True)
        self.assertTrue(any("viac ako nádrž" in w for w in tank["warnings"]))  # 40 l on top of a nearly full tank
        self.assertIn("Nádrž: začiatok", draft["messages"][-1]["text"])
        self.assertTrue(draft["can_approve"])
        self.assertTrue(all(not t["day_confirmed"] for t in draft["trips"]))

        # "V Poprade som bol 14." moves a concrete row; the backend re-checks the odometer anchors.
        poprad = next(t for t in draft["trips"] if t["destination"] == "Poprad")
        # Gemini may also ask for a plan here; an edited schedule must not be regenerated by the chat.
        draft = self.chat(sid, "V Poprade som bol 14.", "Presunul som jazdu do Popradu na 14.", create_plan=True,
                          operations=[{"action": "update_trip", "row": poprad["id"], "day": self.day(14)}])
        moved = next(t for t in draft["trips"] if t["id"] == poprad["id"])
        self.assertEqual((moved["day"], moved["day_confirmed"]), (self.day(14), True))
        self.assertIn(poprad["id"], draft["highlight"])
        self.assert_schedule(draft)

        # Changing a confirmed receipt requires a new review (the checkbox at approval).
        draft = self.chat(sid, "To druhé tankovanie bolo 42 litrov",
                          operations=[{"action": "update_receipt", "row": "B2", "liters": 42}])
        self.assertEqual(next(r for r in draft["receipts"] if r["id"] == "B2")["liters"], "42.000")
        self.assertFalse(draft["receipts_confirmed"])
        # Gemini may not touch rows that do not exist or invent a date outside the month.
        draft = self.chat(sid, "Zmeň J99", operations=[{"action": "update_trip", "row": "J99", "day": self.day(3)},
                                                       {"action": "update_trip", "row": "J1", "day": "2000-01-01"}])
        self.assertIn("J99 v koncepte neexistuje", draft["messages"][-1]["text"])
        draft = self.ok(self.admin.post(f"/api/assistant/{sid}/confirm-receipts"))
        self.assertTrue(draft["can_approve"])

        # Page reload: everything comes back from the server-side draft.
        reloaded = self.ok(self.admin.get(f"/api/assistant/{sid}"))
        self.assertEqual(reloaded["trips"], draft["trips"])
        self.assertEqual(len(reloaded["messages"]), len(draft["messages"]))
        self.assertEqual(self.open()["id"], sid)

        # Another employee cannot see the draft.
        other = TestClient(app, cookies={"kbm_portal_session": "zdenko"})
        self.ok(other.get(f"/api/assistant/{sid}"), 404)

        # Approval: stale version rejected, then one transaction, then idempotent repeat.
        self.ok(self.admin.post(f"/api/assistant/{sid}/approve", json={"version": draft["version"] - 1}), 409)
        saved = self.ok(self.admin.post(f"/api/assistant/{sid}/approve", json={"version": draft["version"]}))
        again = self.ok(self.admin.post(f"/api/assistant/{sid}/approve", json={"version": draft["version"]}))
        self.assertEqual(saved["status"], "saved")
        self.assertEqual(saved["result"], again["result"])
        with Session() as db:
            trips = list(db.scalars(select(Trip).where(Trip.draft_id == saved["result"]["draft_id"])))
            fuels = list(db.scalars(select(Fuel).where(Fuel.draft_id == saved["result"]["draft_id"])))
        self.assertEqual(len(trips), 5)
        self.assertTrue(all(t.estimated for t in trips))
        self.assertEqual(sorted((f.odo is None, str(f.liters)) for f in fuels),
                         [(False, "31.4000000000"), (True, "42.0000000000")])
        self.ok(self.admin.post(f"/api/assistant/{sid}/messages", json={"text": "ešte niečo"}), 409)

        report = self.ok(self.admin.get(f"/api/vehicles/{self.vehicle}/records",
                                        params={"year": self.month.year, "month": self.month.month}))
        self.assertEqual((report["opening_odo"], report["closing_odo"], report["estimated_count"]), (12240, 13280, 5))
        no_odo = next(r for r in report["rows"] if r["type"] == "fuel" and r["odo"] is None)
        self.assertFalse([w for w in no_odo["warnings"] if "Tachometer" in w])  # empty reading is not an error
        self.assertEqual(no_odo["source"], "Bloček zo sprievodcu")
        self.assertFalse([w for r in report["rows"] if r["type"] == "trip" for w in r["warnings"]])
        export = self.admin.get(f"/api/vehicles/{self.vehicle}/export",
                                params={"year": self.month.year, "month": self.month.month, "format": "xlsx"})
        self.assertTrue(export.content.startswith(b"PK"))
        book = load_workbook(io.BytesIO(export.content))
        self.assertEqual(book.sheetnames, ["Mesačný výkaz", "Jazdy", "Tankovanie"])
        report_sheet = book["Mesačný výkaz"]
        cells = {cell.value for row in report_sheet.iter_rows() for cell in row if cell.value is not None}
        self.assertTrue({"Vypracoval:", "Podpis:", "Maroš Sokolák", "V Žiline dňa:", "Prehľad tankovania"} <= cells)
        self.assertEqual(report_sheet.page_setup.orientation, "portrait")
        trips_sheet = book["Jazdy"]
        self.assertEqual(trips_sheet["C3"].value, "Cieľ")
        self.assertTrue(all("odhad" in (trips_sheet.cell(row, 11).value or "").lower() for row in range(4, 9)))
        if Path(os.getenv("PDF_FONT", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")).exists():
            export = self.admin.get(f"/api/vehicles/{self.vehicle}/export",
                                    params={"year": self.month.year, "month": self.month.month, "format": "pdf"})
            self.assertTrue(export.content.startswith(b"%PDF"), export.text)

        self.replace_saved_estimate(sid)

    def replace_saved_estimate(self, old_sid):
        """A month saved earlier as an estimate can be redone; old trips go away only on approval."""
        self.ok(self.admin.delete(f"/api/assistant/{old_sid}"))
        draft = self.open()
        self.assertEqual(draft["existing"], {"count": 5, "replaceable": 5, "manual": 0, "km": 1040, "manual_km": 0})
        self.assertIn("Nahradiť uložený odhad", draft["messages"][-1]["text"])
        sid = draft["id"]
        # Re-entering a receipt that the first run already saved can't be waved through as "not a duplicate".
        draft = self.ok(self.admin.post(f"/api/assistant/{sid}/operations", json={"operations": [
            {"action": "add_receipt", "day": self.day(5), "liters": "31.4", "gross": "51.81", "odo": 12410}]}))
        draft = self.ok(self.admin.post(f"/api/assistant/{sid}/operations",
                                        json={"operations": [{"action": "dismiss_duplicate", "row": "B1"}]}))
        self.assertTrue(any("uložené z mesačného sprievodcu" in p for p in draft["receipt_problems"]))
        self.assertFalse(draft["can_confirm"])
        self.ok(self.admin.post(f"/api/assistant/{sid}/operations", json={"operations": [{"action": "delete_receipt", "row": "B1"}]}))
        draft = self.ok(self.admin.post(f"/api/assistant/{sid}/confirm-receipts"))
        draft = self.chat(sid, "Začiatok 12 240, koniec 13 280. Raz Bratislava, dvakrát Trenčín.",
                          "Harmonogram bol vytvorený.", start_odo=12240, end_odo=13280, create_plan=True,
                          routes=[{"destination": "Bratislava", "count": 1, "approximate_km": 300, "category": "mimo mesto", "days": []},
                                  {"destination": "Trenčín", "count": 2, "category": "mimo mesto", "days": []}])
        self.assertEqual(draft["trips"], [])
        self.assertIn("Nahradiť uložený odhad", draft["messages"][-1]["text"])
        self.assertEqual([r["approximate_km"] for r in draft["routes"]], [400, 160])
        draft = self.ok(self.admin.post(f"/api/assistant/{sid}/operations",
                                        json={"operations": [{"action": "set_replace", "replace": True}]}))
        self.assertTrue(draft["replace_existing"])
        self.assertEqual(len(draft["trips"]), 3)
        self.assertEqual({t["origin"] for t in draft["trips"]}, {"Žilina"})
        self.assert_schedule(draft)
        with Session() as db:  # nothing removed before approval
            self.assertEqual(len(list(db.scalars(select(Trip).where(Trip.vehicle_id == self.vehicle,
                                                                   Trip.day >= self.month)))), 5)
        saved = self.ok(self.admin.post(f"/api/assistant/{sid}/approve", json={"version": draft["version"]}))
        self.assertIn("nahradených 5", saved["messages"][-1]["text"])
        with Session() as db:
            month = list(db.scalars(select(Trip).where(Trip.vehicle_id == self.vehicle, Trip.day >= self.month)))
        self.assertEqual(sorted(t.destination for t in month), ["Bratislava", "Trenčín", "Trenčín"])
        # Manually entered trips are never replaced: they are loaded read-only and stay.
        september = date(self.month.year - (self.month.month == 1), (self.month.month - 2) % 12 + 1, 1)
        other = self.ok(self.admin.post(f"/api/vehicles/{self.vehicle}/assistant", json={"month": september.isoformat()}))
        self.assertEqual(other["existing"]["manual"], 4)
        self.assertEqual(len(other["saved"]["trips"]), 4)
        other = self.ok(self.admin.post(f"/api/assistant/{other['id']}/operations",
                                        json={"operations": [{"action": "set_replace", "replace": True}]}))
        self.assertFalse(other["replace_existing"])
        self.assertEqual(len(other["saved"]["trips"]), 4)

    def assert_schedule(self, draft):
        trips = draft["trips"]
        self.assertEqual(trips[0]["start_odo"], 12240)
        self.assertEqual(trips[-1]["end_odo"], 13280)
        self.assertEqual(sum(t["distance"] for t in trips), 1040)
        for previous, trip in zip(trips, trips[1:]):
            self.assertEqual(previous["end_odo"], trip["start_odo"])
            self.assertLessEqual(previous["day"], trip["day"])
        # Receipt B1 (5th, 12 410 km) and the saved demo fuel (12th, 12 720 km) are hit exactly.
        for number, odo in ((5, 12410), (12, 12720)):
            if not any(t["day"] <= self.day(number) for t in trips):
                continue
            before = [t for t in trips if t["day"] <= self.day(number) and t["end_odo"] <= odo]
            self.assertEqual(before[-1]["end_odo"], odo)
        self.assertEqual(draft["problems"], [])

    def test_gemini_errors_keep_the_draft(self):
        other_month = date(self.month.year - (self.month.month == 1), (self.month.month - 2) % 12 + 1, 1)
        sid = self.ok(self.admin.post(f"/api/vehicles/{self.vehicle}/assistant", json={"month": other_month.isoformat()}))["id"]
        FAKE.error = ApiError(429)
        draft = self.ok(self.admin.post(f"/api/assistant/{sid}/messages", json={"text": "Začiatok 12 000 km"}))
        self.assertIn("limit", draft["error"])
        self.assertEqual([m["role"] for m in draft["messages"][-2:]], ["user", "error"])
        self.ok(self.admin.post(f"/api/assistant/{sid}/files", files={"file": ("b.png", PNG + b"x")}))
        draft = self.ok(self.admin.post(f"/api/assistant/{sid}/extract"))
        self.assertEqual(draft["files"][0]["status"], "error")
        FAKE.error = None
        os.environ["GEMINI_API_KEY"] = ""
        try:
            draft = self.ok(self.admin.post(f"/api/assistant/{sid}/messages", json={"text": "Ahoj"}))
            self.assertIn("GEMINI_API_KEY", draft["error"])
            self.assertFalse(draft["assistant"]["configured"])
        finally:
            os.environ["GEMINI_API_KEY"] = "test-key"
        # Invalid structured output is rejected by Pydantic and reported, not applied.
        FAKE.queue["ReceiptExtraction"].append({"is_receipt": False, "receipts": []})  # pending upload is retried first
        FAKE.queue["ChatResult"].append({"reply": 5, "operations": [{"action": "drop_table"}]})
        draft = self.ok(self.admin.post(f"/api/assistant/{sid}/messages", json={"text": "Test"}))
        self.assertIn("neplatné údaje", draft["error"])
        self.assertEqual(len(self.ok(self.admin.get(f"/api/assistant/{sid}"))["files"]), 1)
        self.ok(self.admin.delete(f"/api/assistant/{sid}"))
        self.ok(self.admin.get(f"/api/assistant/{sid}"), 404)

    def test_vehicle_start_date_and_invalid_gemini_values(self):
        """Review findings: no trips before the start of records, no HTTP 500 on odd Gemini output."""
        vehicle = self.ok(self.admin.post("/api/vehicles", json={
            "name": "Štart v polovici", "registration": "TEST-START", "start_date": self.day(15), "initial_odo": 1000,
            "capacity": 50, "initial_liters": 20, "city_rate": 7, "outside_rate": 6}))
        sid = self.ok(self.admin.post(f"/api/vehicles/{vehicle['id']}/assistant", json={"month": self.month.isoformat()}))["id"]
        self.ok(self.admin.post(f"/api/assistant/{sid}/confirm-receipts"))
        draft = self.chat(sid, "Začiatok -5 km", start_odo=-5)
        self.assertEqual(draft["start_odo"], 1000)  # prefilled from the logbook, the invalid value is ignored
        self.assertIn("mimo povoleného rozsahu", draft["messages"][-1]["text"])
        draft = self.chat(sid, "1000 až 1600, Košice a dvakrát Poprad", start_odo=1000, end_odo=1600, routes=[
            {"destination": "Košice", "count": 1, "category": "mimo mesto", "days": []},
            {"destination": "Poprad", "count": 2, "category": "mimo mesto", "days": []}])
        self.assertEqual(len(draft["trips"]), 3)
        self.assertTrue(all(t["day"] >= self.day(15) for t in draft["trips"]))
        self.assertEqual(draft["problems"], [])
        self.ok(self.admin.post(f"/api/assistant/{sid}/operations", json={"operations": [
            {"action": "add_receipt", "day": self.day(3), "liters": "20", "gross": "33"}]}), 422)
        self.ok(self.admin.post(f"/api/assistant/{sid}/files", files={"file": ("early.png", PNG + b"early")}))
        FAKE.queue["ReceiptExtraction"].append({"is_receipt": True, "receipts": [self.receipt(self.day(3), 20, 33)]})
        draft = self.ok(self.admin.post(f"/api/assistant/{sid}/extract"))
        self.assertTrue(any("pred začiatkom evidencie" in p for p in draft["receipt_problems"]))
        self.assertFalse(draft["can_confirm"])
        saved_draft = self.ok(self.admin.post(f"/api/assistant/{sid}/operations",
                                              json={"operations": [{"action": "delete_receipt", "row": "B1"}]}))
        saved_draft = self.ok(self.admin.post(f"/api/assistant/{sid}/confirm-receipts"))
        saved = self.ok(self.admin.post(f"/api/assistant/{sid}/approve", json={"version": saved_draft["version"]}))
        # The old monthly-draft approval endpoint refuses assistant drafts instead of crashing.
        response = self.admin.post(f"/api/vehicles/{vehicle['id']}/monthly-drafts/{saved['result']['draft_id']}/approve",
                                   json={"reviewed": True, "rows": [{"day": self.day(20), "end_odo": 1100, "category": "mesto"}]})
        self.assertEqual(response.status_code, 409, response.text)

    def test_saved_fuels_and_manual_trips_are_loaded(self):
        """Rows already in the logbook (FleetCor invoice, trips entered by hand) are loaded, sent to Gemini as data,
        counted in the tank, and the draft plans only the kilometres between the kept trips."""
        vehicle = self.ok(self.admin.post("/api/vehicles", json={
            "name": "Ručné jazdy", "registration": "TEST-MAN1", "start_date": self.day(1), "initial_odo": 5000,
            "capacity": 60, "initial_liters": 30, "city_rate": 7, "outside_rate": 6}))
        with Session() as db:
            for number, start, end, place in ((10, 5100, 5200, "Martin"), (20, 5400, 5450, "Ružomberok")):
                db.add(Trip(vehicle_id=vehicle["id"], day=date.fromisoformat(self.day(number)), start_odo=start, end_odo=end,
                            origin="Žilina", destination=place, purpose="Servis", category="mimo mesto", note="", rate=D("6")))
            db.add(Fuel(vehicle_id=vehicle["id"], day=date.fromisoformat(self.day(12)), odo=None, liters=D("40"),
                        gross=D("64.00"), vat=D("23"), station="Shell Žilina", clock="08:00",
                        note="FleetCor 123, poukážka 9"))
            db.commit()
        draft = self.ok(self.admin.post(f"/api/vehicles/{vehicle['id']}/assistant", json={"month": self.month.isoformat()}))
        sid = draft["id"]
        self.assertEqual(draft["start_odo"], 5000)
        self.assertEqual(draft["saved"]["km"], 150)
        self.assertEqual([t["destination"] for t in draft["saved"]["trips"]], ["Martin", "Ružomberok"])
        self.assertEqual([f["note"] for f in draft["saved"]["fuels"]], ["FleetCor 123, poukážka 9"])
        self.assertIn("načítal 1 tankovanie", " ".join(m["text"] for m in draft["messages"]))
        self.assertEqual(draft["tank"]["purchased"], "40.0")
        fuel_key = draft["saved"]["fuels"][0]["key"]
        self.assertIn(fuel_key, draft["tank"]["rows"])

        self.ok(self.admin.post(f"/api/assistant/{sid}/confirm-receipts"))
        draft = self.chat(sid, "Koniec 5 600, dvakrát Košice a raz Poprad.", end_odo=5600, routes=[
            {"destination": "Košice", "count": 2, "category": "mimo mesto", "days": []},
            {"destination": "Poprad", "count": 1, "category": "mimo mesto", "days": []}])
        sent = json.loads(FAKE.calls[-1][2][-1].parts[0].text.split("```json\n", 1)[1].rsplit("\n```", 1)[0])
        self.assertEqual(sent["v_knihe"]["km_ulozenych_jazd"], 150)
        self.assertEqual(sent["v_knihe"]["tankovania"][0]["zdroj"], "FleetCor 123, poukážka 9")
        self.assertEqual(len(draft["trips"]), 3)
        self.assertEqual(draft["problems"], [])
        self.assertEqual(sum(t["distance"] for t in draft["trips"]), 600 - 150)
        rows = sorted([(t["day"], t["start_odo"], t["end_odo"]) for t in draft["trips"]]
                      + [(t["day"], t["start_odo"], t["end_odo"]) for t in draft["saved"]["trips"]])
        odo = 5000
        for _, start, end in rows:
            self.assertEqual(start, odo)
            odo = end
        self.assertEqual(odo, 5600)
        self.assertEqual(draft["planned"], 600)
        self.assertTrue(draft["can_approve"])
        saved = self.ok(self.admin.post(f"/api/assistant/{sid}/approve", json={"version": draft["version"]}))
        self.assertEqual(len(saved["result"]["trip_ids"]), 3)
        with Session() as db:
            month = list(db.scalars(select(Trip).where(Trip.vehicle_id == vehicle["id"]).order_by(Trip.day, Trip.start_odo)))
        self.assertEqual(len(month), 5)
        self.assertEqual(sum(1 for t in month if t.draft_id is None), 2)  # manual trips untouched
        for previous, trip in zip(month, month[1:]):
            self.assertEqual(previous.end_odo, trip.start_odo)

    def test_closing_reading_with_date(self):
        """The closing odometer may be read on another day: inside the month trips stop there, after the month the
        state on the last day is estimated by working days. Approval needs the receipt review checkbox."""
        from datetime import timedelta
        vehicle = self.ok(self.admin.post("/api/vehicles", json={
            "name": "Odčítanie", "registration": "TEST-READ", "start_date": self.day(1), "initial_odo": 5000,
            "capacity": 60, "initial_liters": 30, "city_rate": 7, "outside_rate": 6}))
        sid = self.ok(self.admin.post(f"/api/vehicles/{vehicle['id']}/assistant", json={"month": self.month.isoformat()}))["id"]
        last = (self.month.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
        read = min(last + timedelta(days=3), date.today())
        # "Vymysli jazdy" without the closing reading: nothing is planned and the user is told what is missing.
        draft = self.chat(sid, "Vymysli mi jazdy na tento mesiac.", create_plan=True)
        self.assertEqual(draft["trips"], [])
        self.assertIn("Návrh jázd zatiaľ nie je vytvorený", draft["messages"][-1]["text"])
        draft = self.chat(sid, f"Koniec 6 000 km, odčítané {read}. Trikrát Martin.", end_odo=6000, end_day=read.isoformat(),
                          routes=[{"destination": "Martin", "count": 3, "category": "mimo mesto", "days": []}])
        weekdays = lambda a, b: sum(1 for i in range((b - a).days + 1) if (a + timedelta(days=i)).weekday() < 5)
        expected = 5000 + round(1000 * weekdays(self.month, last) / weekdays(self.month, read))
        self.assertEqual(draft["closing"], {"day": last.isoformat(), "odo": expected, "estimated": True})
        self.assertEqual(draft["distance"], expected - 5000)
        self.assertEqual(sum(t["distance"] for t in draft["trips"]), expected - 5000)
        self.assertIn("odhadol", " ".join(m["text"] for m in draft["messages"][-2:]))
        self.assertEqual(len(draft["days"]), last.day)

        # Read on the 20th: trips only up to that day, later days are marked.
        draft = self.ok(self.admin.post(f"/api/assistant/{sid}/operations", json={"operations": [
            {"action": "set_odometers", "end_day": self.day(20)}]}))
        self.assertEqual(draft["closing"], {"day": self.day(20), "odo": 6000, "estimated": False})
        self.assertTrue(all(t["day"] <= self.day(20) for t in draft["trips"]), draft["trips"])
        self.assertEqual(draft["problems"], [])
        self.assertTrue(draft["days"][20]["after_reading"] and not draft["days"][19]["after_reading"])
        self.ok(self.admin.post(f"/api/assistant/{sid}/operations", json={"operations": [
            {"action": "set_odometers", "end_day": "2000-01-01"}]}), 422)

        draft = self.ok(self.admin.post(f"/api/assistant/{sid}/operations", json={"operations": [
            {"action": "add_receipt", "day": self.day(5), "liters": "20", "gross": "33"}]}))
        self.assertTrue(draft["can_approve"])
        self.ok(self.admin.post(f"/api/assistant/{sid}/approve", json={"version": draft["version"]}), 409)
        saved = self.ok(self.admin.post(f"/api/assistant/{sid}/approve",
                                        json={"version": draft["version"], "receipts_checked": True}))
        self.assertEqual(saved["status"], "saved")
        with Session() as db:
            trips = list(db.scalars(select(Trip).where(Trip.vehicle_id == vehicle["id"])))
        self.assertEqual(max(t.end_odo for t in trips), 6000)

    def test_invent_trips_from_history(self):
        """„Vymysli jazdy“: when the model returns no destinations, the vehicle's frequent ones are used."""
        from datetime import timedelta
        previous = (self.month - timedelta(days=1)).replace(day=1)
        vehicle = self.ok(self.admin.post("/api/vehicles", json={
            "name": "História", "registration": "TEST-HIST", "start_date": previous.isoformat(), "initial_odo": 5000,
            "capacity": 60, "initial_liters": 30, "city_rate": 7, "outside_rate": 6}))
        with Session() as db:
            for number, start, end, place in ((3, 5000, 5100, "Martin"), (10, 5100, 5200, "Martin"), (17, 5200, 5480, "Košice")):
                db.add(Trip(vehicle_id=vehicle["id"], day=previous.replace(day=number), start_odo=start, end_odo=end,
                            origin="Žilina", destination=place, purpose="", category="mimo mesto", note="", rate=D("6")))
            db.commit()
        sid = self.ok(self.admin.post(f"/api/vehicles/{vehicle['id']}/assistant", json={"month": self.month.isoformat()}))["id"]
        draft = self.chat(sid, "Koniec 6 200, vymysli mi jazdy.", "Navrhujem jazdy.", end_odo=6200, create_plan=True)
        self.assertIn("Ciele som vybral z častých jázd", draft["messages"][-1]["text"])
        self.assertEqual({r["destination"] for r in draft["routes"]}, {"Martin", "Košice"})
        self.assertEqual(sum(t["distance"] for t in draft["trips"]), 6200 - 5480)
        self.assertEqual(draft["problems"], [])

    def test_overloaded_model_falls_back_once(self):
        calls = []

        class Busy:
            def generate_content(self, model, contents, config):
                calls.append(model)
                if len(calls) == 1:
                    raise ApiError(503)
                return SimpleNamespace(text=json.dumps({"reply": "OK", "operations": [], "create_plan": False}))
        original = gemini.client_factory
        gemini.client_factory = lambda: SimpleNamespace(models=Busy())
        try:
            result = gemini.interpret({}, [], "ahoj")
        finally:
            gemini.client_factory = original
        self.assertEqual(result.reply, "OK")
        self.assertEqual(calls, [gemini.model_name(), gemini.fallback_model()])

    def test_current_month_and_cross_site_writes_rejected(self):
        today = date.today().replace(day=1)
        self.ok(self.admin.post(f"/api/vehicles/{self.vehicle}/assistant", json={"month": today.isoformat()}), 409)
        response = self.admin.post(f"/api/vehicles/{self.vehicle}/assistant", json={"month": self.month.isoformat()},
                                   headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()

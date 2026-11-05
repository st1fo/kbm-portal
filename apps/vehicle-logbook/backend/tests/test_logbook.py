import io
import os
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from openpyxl import load_workbook

sandbox = TemporaryDirectory()
os.environ["DATABASE_URL"] = os.environ.get("LOGBOOK_TEST_DATABASE_URL") or f"sqlite:///{sandbox.name}/test.sqlite"
os.environ["COOKIE_SECURE"] = "0"

from app.auth import hash_password
from app.db import Session, User
from app.main import app

D = Decimal
ADMIN = {"username":"tester", "password":"only-for-testing"}
VEHICLE = {"name":"Test", "registration":"TEST-1", "start_date":"2024-01-01", "initial_odo":100,
           "initial_liters":20, "initial_net":30, "capacity":60, "city_rate":8, "outside_rate":6}
BASE = Path(__file__).resolve().parents[1]

class LogbookTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        config = Config(str(BASE / "alembic.ini"))
        command.upgrade(config, "head")
        command.check(config)
        with Session() as db:
            db.add(User(username=ADMIN["username"], password=hash_password(ADMIN["password"]), admin=True))
            db.commit()

    def setUp(self):
        self.client = TestClient(app)
        self.assertEqual(self.client.post("/api/login", json=ADMIN).status_code, 200)
        self.addCleanup(self.client.close)

    def assert_status(self, response, expected=200):
        self.assertEqual(response.status_code,expected,response.text)
        return response.json()

    def add_vehicle(self, suffix):
        vehicle = self.assert_status(self.client.post("/api/vehicles",json={**VEHICLE,"registration":"TEST-"+suffix}))
        return f"/api/vehicles/{vehicle['id']}"

    def trip(self,path,end,start=None,day="2024-01-15"):
        return self.client.post(path+"/trips",json={"day":day,"start_odo":start,"end_odo":end,"category":"mesto"})

    def period(self,path,month=1,year=2024):
        return self.assert_status(self.client.get(path+"/records",params={"year":year,"month":month}))

    def test_trips_and_fuel_recompute(self):
        path = self.add_vehicle("LEDGER")
        self.assert_status(self.trip(path,90),422)
        self.assert_status(self.trip(path,150,99),422)
        first = self.assert_status(self.trip(path,150,100))["id"]
        second = self.assert_status(self.trip(path,200,150,"2024-02-01"))["id"]
        purchase = {"day":"2024-01-15","odo":150,"liters":"10","unit_price":"1.80","vat":"20"}
        fuel = self.assert_status(self.client.post(path+"/fuel",json=purchase))["id"]
        self.assert_status(self.client.post(path+"/fuel",json=purchase),409)
        january = self.period(path)
        february = self.period(path,2)
        self.assertEqual(january["distance"],50)
        self.assertEqual(D(january["calculated"]),D("4"))
        self.assertEqual(D(january["purchase_net"]),D("15"))
        self.assertEqual(D(january["closing_liters"]),D("26"))
        self.assertEqual(D(february["opening_liters"]),D("26"))
        self.assertEqual(D(february["closing_liters"]),D("22"))
        self.assert_status(self.client.put(path+f"/trips/{first}",json={
            "day":"2024-01-15","start_odo":100,"end_odo":160,"category":"mesto"}))
        self.assertEqual(self.period(path,2)["rows"][0]["start_odo"],160)
        self.assert_status(self.client.put(path+f"/fuel/{fuel}",json={**purchase,"odo":160,"liters":11}))
        self.assert_status(self.client.delete(path+f"/fuel/{fuel}"))
        self.assert_status(self.client.delete(path+f"/trips/{second}"))
        self.assertEqual(self.period(path,2)["distance"],0)

    def test_closing_permissions_and_snapshots(self):
        path = self.add_vehicle("CLOSE")
        identifier = int(path.rsplit("/",1)[1])
        self.assert_status(self.trip(path,120))
        body = {"username":"employee", "password":"test-password-employee","vehicle_ids":[identifier]}
        self.assert_status(self.client.post("/api/users",json=body))
        other_path = self.add_vehicle("PRIVATE")
        with TestClient(app) as regular, TestClient(app) as anonymous:
            self.assertEqual(anonymous.get(path+"/records",params={"year":2024,"month":1}).status_code,401)
            self.assertEqual(regular.post("/api/login",json={"username":"employee","password":"wrong"}).status_code,401)
            self.assertEqual(regular.post("/api/login",json={k:body[k] for k in ("username","password")}).status_code,200)
            self.assertEqual(regular.get(other_path+"/reports",params={"year":2024}).status_code,404)
            self.assertEqual(regular.post(path+"/close",params={"month":"2024-02-01"}).status_code,403)
            self.assert_status(self.client.post(path+"/close",params={"month":"2024-02-01"}))
            self.assertTrue(self.period(path)["closed"])
            self.assertTrue(self.period(path,2)["closed"])
            self.assertEqual(regular.post(path+"/trips",json={"day":"2024-01-20","end_odo":130,"category":"mesto"}).status_code,409)
            self.assert_status(self.client.post(path+"/reopen",params={"month":"2024-01-01"}),409)
            self.assert_status(self.client.post(path+"/reopen",params={"month":"2024-02-01"}))
            self.assertTrue(self.period(path)["closed"])
            self.assertFalse(self.period(path,2)["closed"])
            self.assertEqual(regular.post("/api/logout").status_code,200)
            self.assertEqual(regular.get("/api/me").status_code,401)

    def test_export_and_formula_injection(self):
        path = self.add_vehicle("EXPORT")
        self.assert_status(self.client.post(path+"/trips",json={
            "day":"2024-01-15","end_odo":120,"category":"mimo mesto","destination":"=1+1"}))
        for month in (1,None):
            for extension in ("pdf","xlsx"):
                query = {"year":2024,"format":extension}
                if month:
                    query["month"] = month
                response = self.client.get(path+"/export",params=query)
                self.assertEqual(response.status_code,200)
                if extension == "pdf":
                    self.assertTrue(response.content.startswith(b"%PDF"))
                else:
                    workbook = load_workbook(io.BytesIO(response.content),read_only=True)
                    self.assertEqual(workbook["Jazdy"]["C4"].value,"=1+1")
                    self.assertEqual(workbook["Jazdy"]["C4"].data_type,"s")
                    workbook.close()

    def test_import_original_and_compare_excel(self):
        source = os.environ.get("ORIGINAL_XLSM")
        if not source:
            self.skipTest("Set ORIGINAL_XLSM to the original workbook to verify historical results.")
        data = Path(source).read_bytes()
        file = {"file":("vzor_cesr.xlsm",data)}
        self.assert_status(self.client.post("/api/import",files={"file":("bad.xlsm",b"invalid")}),422)
        preview = self.assert_status(self.client.post("/api/import",files=file))
        self.assertEqual((len(preview["trips"]),len(preview["purchases"]),len(preview["destinations"])),(124,38,59))
        self.assertTrue(any("riadok 269" in warning for warning in preview["warnings"]))
        params = {"apply":True,"expected_digest":preview["digest"]}
        result = self.assert_status(self.client.post("/api/import",params=params,files=file))
        self.assert_status(self.client.post("/api/import",params=params,files=file),409)
        path = f"/api/vehicles/{result['vehicle_id']}"
        original = load_workbook(io.BytesIO(data),read_only=True,data_only=True)
        self.addCleanup(original.close)
        september = self.period(path,9,2026)
        monthly = original["mesačný výkaz"]
        for field,cell in {"distance":"E13","city_km":"I13","purchased":"G21","gross":"K39",
                           "consumption":"G22","opening_liters":"G19","closing_liters":"G20"}.items():
            self.assertAlmostEqual(float(september[field]),float(monthly[cell].value),places=2)
        reports = self.assert_status(self.client.get(path+"/reports",params={"year":2026}))
        annual = original["ročný výkaz"]
        self.assertEqual(sum(r["distance"] for r in reports),annual["E13"].value-1)
        self.assertAlmostEqual(sum(float(r["gross"]) for r in reports),annual["K21"].value,places=2)
        self.assertAlmostEqual(sum(float(r["purchased"]) for r in reports),annual["G21"].value,places=2)
        rows = list(original["cesty"].iter_rows(min_row=2,max_row=268,max_col=22,values_only=True))
        for index in (2,4,7,8):
            selected = [row for row in rows if isinstance(row[21], date) and row[21].month == index]
            self.assertEqual(reports[index-1]["distance"],sum(row[6] for row in selected))
            self.assertAlmostEqual(float(reports[index-1]["closing_liters"]),selected[-1][18],places=2)

    def test_monthly_draft_review_anchors_and_idempotency(self):
        path = self.add_vehicle("MONTHLY")
        for day, odo in ((10,180),(20,250)):
            self.assert_status(self.client.post(path+"/fuel",json={
                "day":f"2024-01-{day}","odo":odo,"liters":5,"gross":9,"vat":20}))
        body = {"month":"2024-01-01","start_odo":100,"end_odo":300,
                "routes":[{"destination":"Klient", "count":4,"approximate_km":50}]}
        preview = self.assert_status(self.client.post(path+"/monthly-drafts",json=body))
        self.assertEqual(sum(row['distance'] for row in preview['rows']),200)
        self.assertEqual(self.period(path)['trip_count'],0)
        self.assertEqual(len(self.period(path)['rows']),2)
        reviewed = {"rows":preview['rows'],"reviewed":True}
        invalid = {**reviewed,"rows":[{**row,"day":"2024-01-31"} for row in preview['rows']]}
        self.assert_status(self.client.post(path+f"/monthly-drafts/{preview['draft_id']}/approve",json=invalid),409)
        self.assertEqual(self.period(path)['trip_count'],0)
        result = self.assert_status(self.client.post(path+f"/monthly-drafts/{preview['draft_id']}/approve",json=reviewed))
        again = self.assert_status(self.client.post(path+f"/monthly-drafts/{preview['draft_id']}/approve",json=reviewed))
        self.assertEqual(result,again)
        report = self.period(path)
        self.assertEqual(report['trip_count'],4)
        self.assertEqual(report['estimated_count'],4)
        self.assertEqual(report['distance'],200)
        self.assertTrue(all(row['source']=='Mesačný odhad' for row in report['rows'] if row['type']=='trip'))
        self.assert_status(self.client.post(path+"/monthly-drafts",json=body),409)
        export = self.client.get(path+'/export',params={'year':2024,'month':1,'format':'xlsx'})
        workbook = load_workbook(io.BytesIO(export.content),read_only=True)
        sheet=workbook['Jazdy']
        headers=[cell.value for cell in sheet[2]]
        self.assertEqual(sheet.cell(3,headers.index('Pôvod záznamu')+1).value,'Mesačný odhad')
        workbook.close()

    def test_monthly_draft_stale_permissions_and_catalogue(self):
        path = self.add_vehicle('STALE')
        vehicle_id = int(path.rsplit('/',1)[1])
        self.assert_status(self.client.post(path+'/destinations',json={'name':'Košice','distance':50}))
        body = {'month':'2024-01-01','start_odo':100,'end_odo':200,
                'routes':[{'destination':'Košice','count':2}]}
        preview = self.assert_status(self.client.post(path+'/monthly-drafts',json=body))
        self.assertEqual(len(preview['rows']),2)
        self.assertTrue(all(row['approximate_km']==100 for row in preview['rows']))
        self.assert_status(self.client.post('/api/users',json={'username':'draft-user','password':'draft-password-123','vehicle_ids':[vehicle_id]}))
        with TestClient(app) as other:
            self.assertEqual(other.post('/api/login',json={'username':'draft-user','password':'draft-password-123'}).status_code,200)
            self.assertEqual(other.post(path+f"/monthly-drafts/{preview['draft_id']}/approve",json={
                'reviewed':True,'rows':preview['rows']}).status_code,404)
            self.assertEqual(other.post('/api/demo').status_code,403)
        self.assert_status(self.client.post(path+'/fuel',json={'day':'2024-01-31','odo':200,'liters':5,'gross':8,'vat':20}))
        self.assert_status(self.client.post(path+f"/monthly-drafts/{preview['draft_id']}/approve",json={
            'reviewed':True,'rows':preview['rows']}),409)
        self.assertEqual(self.period(path)['trip_count'],0)
        unknown={**body,'routes':[{'destination':'Neznámy cieľ'}]}
        self.assert_status(self.client.post(path+'/monthly-drafts',json=unknown),409)
        wrong={**body,'start_odo':110}
        self.assert_status(self.client.post(path+'/monthly-drafts',json=wrong),409)
        self.assert_status(self.client.post(path+'/close',params={'month':'2024-01-01'}))
        self.assert_status(self.client.post(path+'/monthly-drafts',json=body),409)

    def test_demo_can_generate_complete_month(self):
        first = self.assert_status(self.client.post('/api/demo'))
        second = self.assert_status(self.client.post('/api/demo'))
        self.assertEqual(first['vehicle_id'],second['vehicle_id'])
        path=f"/api/vehicles/{first['vehicle_id']}"
        month=f"{first['year']}-{first['month']:02}-01"
        body={'month':month,'start_odo':first['start_odo'],'end_odo':first['end_odo'],
              'routes':[{'destination':'Košice','count':6},{'destination':'Prešov','count':3},
                        {'destination':'Poprad','count':4},{'destination':'Mesto','count':3,'category':'mesto'}]}
        preview=self.assert_status(self.client.post(path+'/monthly-drafts',json=body))
        self.assertEqual(sum(row['distance'] for row in preview['rows']),1040)
        self.assertEqual(len(preview['rows']),16)
        self.assertEqual(preview['rows'],sorted(preview['rows'],key=lambda r:(r['day'],r['end_odo'])))
        self.assert_status(self.client.post(path+f"/monthly-drafts/{preview['draft_id']}/approve",json={
            'rows':preview['rows'],'reviewed':True}))
        report=self.period(path,first['month'],first['year'])
        self.assertEqual(report['distance'],1040)
        self.assertFalse([warning for row in report['rows'] for warning in row['warnings'] if 'Tachometer' in warning])

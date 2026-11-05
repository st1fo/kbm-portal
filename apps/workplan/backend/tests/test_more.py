from test_workplan import client, auth, plan

def test_office_with_approval_cannot_bypass_request(client):
    h=auth(client)
    p={'name':'Office','icon':'Building2','color':'#123456','requires_approval':True,'active':True}
    assert client.put('/api/types/1',headers=h,json=p).status_code==200
    assert client.post('/api/attendance',headers=auth(client,2),json=plan()).status_code==403

def test_admin_cannot_create_or_rename_portal_accounts(client):
    # Pôvodne vytváranie účtov; prototyp má štyri pevné účty portálu, identitu spravuje portál.
    h=auth(client)
    p={'name':'New User','email':'new@test.local','password':'longpassword-test'}
    assert client.post('/api/employees',headers=h,json=p).status_code==409
    p={'name':'Renamed','email':'u2@test.local','role':'employee','active':True,'daily_hours':8}
    assert client.put('/api/employees/2',headers=h,json=p).status_code==409

def test_request_blocks_planning_and_audit(client):
    h=auth(client,2)
    assert client.post('/api/requests',headers=h,json=plan(type_id=3)).status_code==200
    assert client.post('/api/attendance',headers=h,json=plan()).status_code==409
    logs=client.get('/api/audit',headers=auth(client)).json()
    assert any(x['action']=='request.create' for x in logs)

def test_xlsx_summary_and_invalid_format(client):
    import io
    from openpyxl import load_workbook
    h=auth(client)
    uri='/api/reports?start=2026-11-01&end=2026-11-30'
    r=client.get(uri+'&format=xlsx',headers=h)
    wb=load_workbook(io.BytesIO(r.content));assert 'Súhrn' in wb.sheetnames
    assert client.get(uri+'&format=html',headers=h).status_code==422

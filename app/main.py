from fastapi import FastAPI,Request,UploadFile,File,Form,HTTPException
from fastapi.responses import HTMLResponse,RedirectResponse,FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv
from pathlib import Path
import shutil,tempfile,os,json

load_dotenv()
from app.db import init_db,connect
from app.auth import ensure_default_admin,authenticate
from app.services.ingestion import ingest_file
from app.services.orchestrator import process_invoice
from app.services.workflow import approve_invoice,reject_invoice
from app.services.outbound import create_outbound
from app.services.reminders import overdue_outbound,make_reminder_draft
from app.services.integrations import status as integrations_status
from app.connectors.gmail_oauth import import_attachments
from app.connectors.bank_csv import import_bank_csv,propose_matches
from app.connectors.accounting import export_ebp_csv

BASE=Path(__file__).resolve().parents[1]
UPLOADS=BASE/"data"/"uploads";EXPORTS=BASE/"data"/"exports"
app=FastAPI(title="AURELIA V5",version="5.0.0")
app.add_middleware(SessionMiddleware,secret_key=os.getenv("AURELIA_SESSION_SECRET","DEV-CHANGE-ME-"+("x"*32)))
app.mount("/static",StaticFiles(directory=str(Path(__file__).parent/"static")),name="static")
templates=Jinja2Templates(directory=str(Path(__file__).parent/"templates"))

@app.on_event("startup")
def startup():init_db();ensure_default_admin()

def user(request):return request.session.get("user")
def require(request):
    u=user(request)
    if not u:raise HTTPException(401,"Connexion requise")
    return u

@app.get("/login",response_class=HTMLResponse)
def login_page(request:Request):return templates.TemplateResponse("login.html",{"request":request,"error":None})
@app.post("/login")
def login(request:Request,username:str=Form(...),password:str=Form(...)):
    u=authenticate(username,password)
    if not u:return templates.TemplateResponse("login.html",{"request":request,"error":"Identifiants incorrects"},status_code=401)
    request.session["user"]={"username":u["username"],"role":u["role"]};return RedirectResponse("/",303)
@app.get("/logout")
def logout(request:Request):request.session.clear();return RedirectResponse("/login",303)

@app.get("/",response_class=HTMLResponse)
def dashboard(request:Request):
    u=user(request)
    if not u:return RedirectResponse("/login",303)
    con=connect()
    stats=dict(con.execute("""SELECT COUNT(*) total,SUM(status='APPROVED') approved,SUM(status='REVIEW_REQUIRED') reviews,
      SUM(payment_status='UNPAID') unpaid FROM invoices""").fetchone())
    latest=[dict(r) for r in con.execute("SELECT * FROM invoices ORDER BY id DESC LIMIT 15")]
    customers=[dict(r) for r in con.execute("SELECT * FROM customers ORDER BY name")]
    con.close()
    return templates.TemplateResponse("dashboard.html",{"request":request,"user":u,"stats":stats,"latest":latest,
        "integrations":integrations_status(),"overdue":overdue_outbound(),"customers":customers})

@app.post("/api/invoices/upload")
async def api_upload(request:Request,file:UploadFile=File(...)):
    u=require(request);ext=Path(file.filename).suffix.lower()
    if ext not in {".pdf",".xml",".ubl",".cii"}:raise HTTPException(400,"Format non supporté")
    UPLOADS.mkdir(parents=True,exist_ok=True);target=UPLOADS/Path(file.filename).name
    with target.open("wb") as f:shutil.copyfileobj(file.file,f)
    data=ingest_file(target);return {"invoice":data,"decision":process_invoice(data,u["username"])}

@app.post("/api/invoices/process")
def api_process(request:Request,data:dict):u=require(request);return process_invoice(data,u["username"])

@app.post("/api/gmail/import")
def gmail_import(request:Request):
    u=require(request);paths=import_attachments(UPLOADS);results=[]
    for p in paths:
        data=ingest_file(p);results.append(process_invoice(data,u["username"]))
    return {"downloaded":len(paths),"processed":results}

@app.post("/api/outbound")
def outbound(request:Request,customer_id:int=Form(...),description:str=Form(...),net_amount:float=Form(...),vat_rate:float=Form(20),due_days:int=Form(30)):
    require(request);return create_outbound(customer_id,description,net_amount,vat_rate,due_days)

@app.post("/api/customers/{outbound_id}/reminder-draft")
def reminder(request:Request,outbound_id:int):
    require(request);return make_reminder_draft(outbound_id)

@app.post("/api/bank/import")
async def bank_import(request:Request,file:UploadFile=File(...)):
    require(request)
    with tempfile.NamedTemporaryFile(delete=False,suffix=".csv") as t:shutil.copyfileobj(file.file,t);p=t.name
    return {"imported":import_bank_csv(p)}

@app.get("/api/bank/matches")
def bank_matches(request:Request):require(request);return propose_matches()

@app.get("/api/integrations/status")
def int_status(request:Request):require(request);return integrations_status()

@app.get("/export/ebp")
def ebp(request:Request):
    require(request);path=EXPORTS/"ebp_ecritures.csv";export_ebp_csv(path)
    return FileResponse(path,filename="ebp_ecritures.csv",media_type="text/csv")

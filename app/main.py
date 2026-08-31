from fastapi import BackgroundTasks,FastAPI,Request,UploadFile,File,Form,HTTPException
from fastapi.responses import HTMLResponse,RedirectResponse,FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote
import logging,shutil,tempfile,os,json

load_dotenv()
from app.local_config import ensure_local_directories,load_local_config
from app.resource_paths import program_directory,resource_path
from app.services.data_migration import migrate_legacy_database
from app.services.session_secret import get_or_create_session_secret
from app.version import APP_VERSION

BASE=program_directory()
LOCAL_CONFIG=load_local_config(BASE)
ensure_local_directories(LOCAL_CONFIG)
migrate_legacy_database(LOCAL_CONFIG)

from app.db import init_db,connect
from app.auth import authenticate,change_password,has_active_admin
from app.services.ingestion import ingest_file
from app.services.orchestrator import process_invoice
from app.services.document_import import SUPPORTED_DOCUMENT_EXTENSIONS,import_document
from app.services.workflow import approve_invoice,correct_invoice,reject_invoice,record_audit,REJECTION_REASONS
from app.services.outbound import create_outbound
from app.services.reminders import overdue_outbound,make_reminder_draft
from app.services.integrations import status as integrations_status
from app.services.company import get_active_company,save_active_company
from app.services.ocr import configure_tesseract,ocr_status
from app.services.email_import import import_eml
from app.services.supplier_banks import accept_supplier_bank_account,list_supplier_bank_accounts,reject_supplier_bank_account
from app.services.invoice_review import dashboard_data,get_document_detail,get_invoice_detail,list_invoices
from app.connectors.gmail_oauth import import_attachments
from app.connectors.bank_csv import bank_overview,import_bank_csv,propose_matches
from app.connectors.accounting import export_ebp_csv
from app.services.payments import (cancel_payment_match,ignore_transaction,payment_overview,
    set_account_final,validate_payment_match)
from app.services.accounting_exports import (accounting_config,create_accounting_export,
    export_preview,save_accounting_config)
from app.services.folder_watcher import FolderWatcher
from app.services.local_ingestion import unique_destination
from app.services.local_logging import close_local_logging,configure_local_logging
from app.services.onboarding import create_initial_setup
from app.services.sqlite_backups import BackupScheduler,backup_if_due

UPLOADS=LOCAL_CONFIG.uploads_dir
EXPORTS=LOCAL_CONFIG.exports_dir

@asynccontextmanager
async def lifespan(app):
    config=ensure_local_directories(LOCAL_CONFIG);configure_local_logging(config)
    logger=logging.getLogger("aurelia.runtime")
    logger.info("aurelia_starting legacy_data_layout=%s",config.legacy_data_layout)
    if config.backups_enabled:
        try:backup_if_due(config,reason="pre-startup")
        except Exception as exc:logger.exception("pre_startup_backup_failed error=%s",type(exc).__name__)
    init_db()
    watcher=FolderWatcher(config) if config.watcher_enabled else None
    scheduler=BackupScheduler(config) if config.backups_enabled else None
    if watcher:watcher.start()
    if scheduler:scheduler.start()
    app.state.local_config=config;app.state.folder_watcher=watcher;app.state.backup_scheduler=scheduler
    logger.info("aurelia_started watcher=%s backups=%s",bool(watcher),bool(scheduler))
    try:yield
    finally:
        if watcher:watcher.stop()
        if scheduler:scheduler.stop()
        logger.info("aurelia_stopped")
        close_local_logging()

app=FastAPI(title="AURELIA V5",version=APP_VERSION,lifespan=lifespan)
app.add_middleware(SessionMiddleware,secret_key=get_or_create_session_secret(LOCAL_CONFIG))
app.mount("/static",StaticFiles(directory=str(resource_path("app","static"))),name="static")
templates=Jinja2Templates(directory=str(resource_path("app","templates")))

def user(request):return request.session.get("user")
def require(request):
    u=user(request)
    if not u:raise HTTPException(401,"Connexion requise")
    if u.get("must_change_password"):raise HTTPException(403,"Changement de mot de passe requis")
    return u
def require_admin(request):
    u=require(request)
    if u.get("role")!="admin":raise HTTPException(403,"Accès administrateur requis")
    return u

@app.get("/health")
def health():return {"app":"aurelia","version":APP_VERSION}

@app.get("/setup",response_class=HTMLResponse)
def setup_page(request:Request):
    if has_active_admin():return RedirectResponse("/login",303)
    return templates.TemplateResponse(request,"setup.html",{"error":None,"values":{}})

@app.post("/setup")
def setup_create(
    request:Request,username:str=Form(...),password:str=Form(...),
    password_confirmation:str=Form(...),legal_name:str=Form(...),tax_id:str=Form(...),
    country:str=Form(...),currency:str=Form("EUR")
):
    if has_active_admin():return RedirectResponse("/login",303)
    values={"username":username,"legal_name":legal_name,"tax_id":tax_id,
            "country":country,"currency":currency}
    try:
        create_initial_setup(username,password,password_confirmation,legal_name,tax_id,country,currency)
    except ValueError as exc:
        return templates.TemplateResponse(request,"setup.html",{
            "error":str(exc),"values":values},status_code=400)
    return RedirectResponse("/login?configured=1",303)

@app.get("/login",response_class=HTMLResponse)
def login_page(request:Request,configured:int=0):
    if not has_active_admin():return RedirectResponse("/setup",303)
    return templates.TemplateResponse(request,"login.html",{"error":None,"configured":bool(configured)})
@app.post("/login")
def login(request:Request,username:str=Form(...),password:str=Form(...)):
    if not has_active_admin():return RedirectResponse("/setup",303)
    u=authenticate(username,password)
    if not u:return templates.TemplateResponse(request,"login.html",{"error":"Identifiants incorrects"},status_code=401)
    request.session["user"]={"username":u["username"],"role":u["role"],
        "must_change_password":bool(u.get("must_change_password"))}
    return RedirectResponse("/change-password" if u.get("must_change_password") else "/",303)
@app.get("/logout")
def logout(request:Request):request.session.clear();return RedirectResponse("/login",303)

@app.post("/shutdown",response_class=HTMLResponse)
def shutdown(request:Request,background_tasks:BackgroundTasks):
    require_admin(request)
    callback=getattr(request.app.state,"shutdown_callback",None)
    if callback is None:raise HTTPException(503,"Arrêt applicatif indisponible")
    background_tasks.add_task(callback)
    request.session.clear()
    return HTMLResponse("<!doctype html><html><body><h1>Aurelia est arrêtée.</h1><p>Vous pouvez fermer cette page.</p></body></html>")

@app.get("/change-password",response_class=HTMLResponse)
def password_change_page(request:Request):
    u=user(request)
    if not u:return RedirectResponse("/login",303)
    return templates.TemplateResponse(request,"change_password.html",{"user":u,"error":None})

@app.post("/change-password")
def password_change(request:Request,current_password:str=Form(...),new_password:str=Form(...),
                    confirmation:str=Form(...)):
    u=user(request)
    if not u:return RedirectResponse("/login",303)
    try:change_password(u["username"],current_password,new_password,confirmation)
    except ValueError as exc:return templates.TemplateResponse(request,"change_password.html",{
        "user":u,"error":str(exc)},status_code=400)
    request.session.clear();return RedirectResponse("/login?configured=1",303)

@app.get("/",response_class=HTMLResponse)
def dashboard(request:Request):
    u=user(request)
    if not u:return RedirectResponse("/login",303)
    if u.get("must_change_password"):return RedirectResponse("/change-password",303)
    con=connect()
    stats=dashboard_data()
    latest=[dict(r) for r in con.execute("SELECT * FROM invoices ORDER BY id DESC LIMIT 15")]
    customers=[dict(r) for r in con.execute("SELECT * FROM customers ORDER BY name")]
    con.close()
    return templates.TemplateResponse(request,"dashboard.html",{"user":u,"stats":stats,"latest":latest,
        "integrations":integrations_status(),"overdue":overdue_outbound(),"customers":customers,
        "payments":payment_overview(),"company":get_active_company()})

@app.get("/invoices",response_class=HTMLResponse)
def invoices_page(request:Request,status:str="",direction:str="",supplier:str="",date_from:str="",
                  date_to:str="",document_type:str="",alert:str=""):
    u=require(request)
    filters={"status":status,"direction":direction,"supplier":supplier,"date_from":date_from,
             "date_to":date_to,"document_type":document_type,"alert":alert}
    invoices,suppliers=list_invoices(filters)
    return templates.TemplateResponse(request,"invoice_list.html",{
        "user":u,"invoices":invoices,"suppliers":suppliers,"filters":filters})

@app.get("/invoices/{invoice_id}",response_class=HTMLResponse)
def invoice_detail_page(request:Request,invoice_id:int,message:str="",error:str=""):
    u=require(request);invoice=get_invoice_detail(invoice_id)
    if not invoice:raise HTTPException(404,"Facture introuvable")
    return templates.TemplateResponse(request,"invoice_detail.html",{
        "user":u,"invoice":invoice,"message":message,"error":error,
        "rejection_reasons":REJECTION_REASONS})

@app.post("/invoices/{invoice_id}/approve")
def invoice_approve(request:Request,invoice_id:int,comment:str=Form(""),account:str=Form("")):
    u=require(request)
    try:approve_invoice(invoice_id,u["username"],account.strip() or None,comment)
    except ValueError as exc:return RedirectResponse(f"/invoices/{invoice_id}?error={quote(str(exc))}",303)
    return RedirectResponse(f"/invoices/{invoice_id}?message={quote('Facture validée')}",303)

@app.post("/invoices/{invoice_id}/reject")
def invoice_reject(request:Request,invoice_id:int,reason:str=Form(...),comment:str=Form("")):
    u=require(request)
    try:reject_invoice(invoice_id,u["username"],reason,comment)
    except ValueError as exc:return RedirectResponse(f"/invoices/{invoice_id}?error={quote(str(exc))}",303)
    return RedirectResponse(f"/invoices/{invoice_id}?message={quote('Facture rejetée')}",303)

@app.post("/invoices/{invoice_id}/correct")
def invoice_correct(
    request:Request,invoice_id:int,supplier_name:str=Form(""),customer_name:str=Form(""),
    invoice_number:str=Form(""),issue_date:str=Form(""),due_date:str=Form(""),
    net_amount:str=Form(...),vat_amount:str=Form(...),gross_amount:str=Form(...),
    direction:str=Form(...),lines_text:str=Form(""),justification:str=Form(...)
):
    u=require(request);values={name:value for name,value in locals().items() if name in {
        "supplier_name","customer_name","invoice_number","issue_date","due_date","net_amount",
        "vat_amount","gross_amount","direction","lines_text"}}
    try:correct_invoice(invoice_id,u["username"],values,justification)
    except ValueError as exc:return RedirectResponse(f"/invoices/{invoice_id}?error={quote(str(exc))}",303)
    return RedirectResponse(f"/invoices/{invoice_id}?message={quote('Corrections enregistrées')}",303)

@app.post("/invoices/{invoice_id}/rib/{account_id}/accept")
def invoice_rib_accept(request:Request,invoice_id:int,account_id:int):
    u=require_admin(request);invoice=get_invoice_detail(invoice_id)
    if not invoice or not invoice.get("rib_alert") or invoice["rib_alert"].get("pending_account_id")!=account_id:
        raise HTTPException(404,"Alerte RIB introuvable")
    try:accept_supplier_bank_account(account_id,u["username"])
    except ValueError as exc:return RedirectResponse(f"/invoices/{invoice_id}?error={quote(str(exc))}",303)
    record_audit(invoice_id,u["username"],"rib_accepted",details={"account_id":account_id})
    return RedirectResponse(f"/invoices/{invoice_id}?message={quote('Nouveau RIB accepté sans autorisation de paiement')}",303)

@app.post("/invoices/{invoice_id}/rib/{account_id}/reject")
def invoice_rib_reject(request:Request,invoice_id:int,account_id:int):
    u=require_admin(request);invoice=get_invoice_detail(invoice_id)
    if not invoice or not invoice.get("rib_alert") or invoice["rib_alert"].get("pending_account_id")!=account_id:
        raise HTTPException(404,"Alerte RIB introuvable")
    try:reject_supplier_bank_account(account_id,u["username"])
    except ValueError as exc:return RedirectResponse(f"/invoices/{invoice_id}?error={quote(str(exc))}",303)
    record_audit(invoice_id,u["username"],"rib_rejected",details={"account_id":account_id})
    return RedirectResponse(f"/invoices/{invoice_id}?message={quote('Ancien RIB conservé')}",303)

@app.post("/invoices/{invoice_id}/account")
def invoice_account(request:Request,invoice_id:int,account:str=Form(...),reason:str=Form("")):
    u=require(request)
    try:set_account_final(invoice_id,account,u["username"],reason)
    except ValueError as exc:return RedirectResponse(f"/invoices/{invoice_id}?error={quote(str(exc))}",303)
    return RedirectResponse(f"/invoices/{invoice_id}?message={quote('Compte comptable validé')}",303)

@app.get("/documents/{document_id}",response_class=HTMLResponse)
def document_detail_page(request:Request,document_id:int):
    u=require(request);document=get_document_detail(document_id)
    if not document:raise HTTPException(404,"Document introuvable")
    return templates.TemplateResponse(request,"document_detail.html",{"user":u,"document":document})

@app.get("/settings/company",response_class=HTMLResponse)
def company_page(request:Request,saved:int=0):
    u=require_admin(request)
    company=get_active_company()
    if company:
        try:company["aliases_text"]="\n".join(json.loads(company.get("aliases_json") or "[]"))
        except (TypeError,json.JSONDecodeError):company["aliases_text"]=""
    return templates.TemplateResponse(request,"company.html",{
        "user":u,"company":company,"saved":bool(saved),"error":None})

@app.post("/settings/company")
def company_save(
    request:Request,legal_name:str=Form(...),trade_name:str=Form(""),country:str=Form(""),
    currency:str=Form("EUR"),
    address:str=Form(""),postal_code:str=Form(""),city:str=Form(""),vat_id:str=Form(""),
    nif:str=Form(""),siren:str=Form(""),siret:str=Form(""),iban:str=Form(""),bic:str=Form(""),
    email:str=Form(""),phone:str=Form(""),aliases:str=Form("")
):
    u=require_admin(request)
    values={name:value for name,value in locals().items() if name in {
        "legal_name","trade_name","country","address","postal_code","city","vat_id","nif",
        "siren","siret","iban","bic","email","phone","currency"}}
    try:
        save_active_company(values,[line for line in aliases.splitlines() if line.strip()])
    except ValueError as exc:
        company=dict(values,aliases_text=aliases)
        return templates.TemplateResponse(request,"company.html",{
            "user":u,"company":company,"saved":False,"error":str(exc)},status_code=400)
    return RedirectResponse("/settings/company?saved=1",303)

@app.get("/settings/ocr",response_class=HTMLResponse)
def ocr_settings_page(request:Request,saved:int=0):
    u=require_admin(request)
    return templates.TemplateResponse(request,"ocr_settings.html",{
        "user":u,"ocr":ocr_status(),"saved":bool(saved)})

@app.post("/settings/ocr")
def ocr_settings_save(request:Request,tesseract_cmd:str=Form(""),ocr_languages:str=Form("fra+eng+spa")):
    require_admin(request)
    configure_tesseract(tesseract_cmd,ocr_languages)
    return RedirectResponse("/settings/ocr?saved=1",303)

@app.get("/settings/supplier-banks",response_class=HTMLResponse)
def supplier_banks_page(request:Request):
    u=require_admin(request)
    return templates.TemplateResponse(request,"supplier_banks.html",{
        "user":u,"accounts":list_supplier_bank_accounts()})

@app.post("/settings/supplier-banks/{account_id}/accept")
def supplier_bank_accept(request:Request,account_id:int):
    u=require_admin(request)
    try:accept_supplier_bank_account(account_id,u["username"])
    except ValueError as exc:raise HTTPException(404,str(exc))
    return RedirectResponse("/settings/supplier-banks",303)

@app.post("/api/invoices/upload")
async def api_upload(request:Request,file:UploadFile=File(...)):
    u=require(request);ext=Path(file.filename).suffix.lower()
    if ext not in SUPPORTED_DOCUMENT_EXTENSIONS:raise HTTPException(400,"Format non supporté")
    UPLOADS.mkdir(parents=True,exist_ok=True);target=unique_destination(UPLOADS,Path(file.filename).name)
    with target.open("wb") as f:shutil.copyfileobj(file.file,f)
    result=import_document(target,u["username"]);return {"invoice":result["document"],"decision":result["decision"]}

@app.post("/invoices/import")
async def web_invoice_upload(request:Request,file:UploadFile=File(...)):
    u=require(request);ext=Path(file.filename or "").suffix.lower()
    if ext not in SUPPORTED_DOCUMENT_EXTENSIONS:raise HTTPException(400,"Format non supporté")
    UPLOADS.mkdir(parents=True,exist_ok=True);target=unique_destination(UPLOADS,Path(file.filename).name)
    with target.open("wb") as destination:shutil.copyfileobj(file.file,destination)
    result=import_document(target,u["username"]);decision=result["decision"]
    if decision.get("document_id"):
        return RedirectResponse(f"/documents/{decision['document_id']}",303)
    if decision.get("invoice_id"):
        return RedirectResponse(f"/invoices/{decision['invoice_id']}",303)
    raise HTTPException(500,"Le document a été analysé mais aucun dossier n'a été créé")

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
    u=require(request)
    if Path(file.filename or "").suffix.lower()!=".csv":raise HTTPException(400,"Un fichier CSV bancaire est requis")
    with tempfile.NamedTemporaryFile(delete=False,suffix=".csv") as t:shutil.copyfileobj(file.file,t);p=t.name
    try:return {"imported":import_bank_csv(p,file.filename,u["username"])}
    finally:Path(p).unlink(missing_ok=True)

@app.get("/api/bank/matches")
def bank_matches(request:Request):require(request);return propose_matches()

@app.get("/bank",response_class=HTMLResponse)
def bank_page(request:Request,imported:int=0,message:str="",error:str=""):
    u=require(request)
    return templates.TemplateResponse(request,"bank.html",{
        "user":u,"overview":bank_overview(),"imported":imported,"message":message,"error":error})

@app.post("/bank/import")
async def bank_page_import(request:Request,file:UploadFile=File(...)):
    u=require(request)
    if Path(file.filename or "").suffix.lower()!=".csv":raise HTTPException(400,"Un fichier CSV bancaire est requis")
    with tempfile.NamedTemporaryFile(delete=False,suffix=".csv") as temporary:
        shutil.copyfileobj(file.file,temporary);path=temporary.name
    try:rows=import_bank_csv(path,file.filename,u["username"])
    finally:Path(path).unlink(missing_ok=True)
    return RedirectResponse(f"/bank?imported={len(rows)}",303)

@app.post("/bank/transactions/{transaction_id}/match")
def bank_match_validate(request:Request,transaction_id:int,invoice_id:int=Form(...),
                        allocated_amount:str=Form(""),comment:str=Form("")):
    u=require(request)
    try:result=validate_payment_match(transaction_id,invoice_id,u["username"],allocated_amount,comment)
    except ValueError as exc:return RedirectResponse(f"/bank?error={quote(str(exc))}",303)
    return RedirectResponse(f"/bank?message={quote('Rapprochement validé : '+result['payment_status'])}",303)

@app.post("/bank/transactions/{transaction_id}/ignore")
def bank_transaction_ignore(request:Request,transaction_id:int,comment:str=Form("")):
    u=require(request)
    try:ignore_transaction(transaction_id,u["username"],comment)
    except ValueError as exc:return RedirectResponse(f"/bank?error={quote(str(exc))}",303)
    return RedirectResponse(f"/bank?message={quote('Transaction ignorée')}",303)

@app.post("/payments/matches/{match_id}/cancel")
def payment_match_cancel(request:Request,match_id:int,reason:str=Form(...)):
    u=require(request)
    try:cancel_payment_match(match_id,u["username"],reason)
    except ValueError as exc:return RedirectResponse(f"/payments?error={quote(str(exc))}",303)
    return RedirectResponse(f"/payments?message={quote('Rapprochement annulé')}",303)

@app.get("/payments",response_class=HTMLResponse)
def payments_page(request:Request,message:str="",error:str=""):
    u=require(request)
    return templates.TemplateResponse(request,"payments.html",{
        "user":u,"overview":payment_overview(),"message":message,"error":error})

@app.get("/exports/accounting",response_class=HTMLResponse)
def accounting_exports_page(request:Request,date_from:str="",date_to:str="",direction:str="",
                            payment:str="",exported:str="not_exported",message:str="",error:str=""):
    u=require(request);filters={"date_from":date_from,"date_to":date_to,"direction":direction,
                                "payment":payment,"exported":exported}
    return templates.TemplateResponse(request,"accounting_exports.html",{
        "user":u,"preview":export_preview(filters),"filters":filters,"message":message,"error":error})

@app.post("/exports/accounting/config")
def accounting_config_save(request:Request,journal_purchase:str=Form(""),journal_sale:str=Form(""),
    account_supplier:str=Form(""),account_customer:str=Form(""),
    account_vat_deductible:str=Form(""),account_vat_collected:str=Form("")):
    require_admin(request);save_accounting_config({name:value for name,value in locals().items() if name.startswith("journal_") or name.startswith("account_")})
    return RedirectResponse(f"/exports/accounting?message={quote('Configuration comptable enregistrée')}",303)

@app.post("/exports/accounting/create")
def accounting_export_create(request:Request,date_from:str=Form(""),date_to:str=Form(""),
    direction:str=Form(""),payment:str=Form(""),exported:str=Form("not_exported")):
    u=require(request);filters={"date_from":date_from,"date_to":date_to,"direction":direction,
                                "payment":payment,"exported":exported}
    try:result=create_accounting_export(EXPORTS/"ebp_ecritures.csv",filters,u["username"])
    except ValueError as exc:return RedirectResponse(f"/exports/accounting?error={quote(str(exc))}",303)
    return FileResponse(result["path"],filename=Path(result["path"]).name,media_type="text/csv",
                        headers={"X-Aurelia-Export-Batch":result["batch_id"]})

@app.get("/emails",response_class=HTMLResponse)
def emails_page(request:Request):
    u=require(request)
    return templates.TemplateResponse(request,"emails.html",{"user":u,"result":None})

@app.post("/emails/import",response_class=HTMLResponse)
async def emails_import(request:Request,file:UploadFile=File(...)):
    u=require(request)
    if Path(file.filename or "").suffix.lower()!=".eml":raise HTTPException(400,"Un fichier EML est requis")
    with tempfile.NamedTemporaryFile(delete=False,suffix=".eml") as temporary:
        shutil.copyfileobj(file.file,temporary);path=temporary.name
    try:result=import_eml(path,u["username"])
    finally:Path(path).unlink(missing_ok=True)
    return templates.TemplateResponse(request,"emails.html",{"user":u,"result":result})

@app.get("/api/integrations/status")
def int_status(request:Request):require(request);return integrations_status()

@app.get("/export/ebp")
def ebp(request:Request):
    require(request);return RedirectResponse("/exports/accounting",303)

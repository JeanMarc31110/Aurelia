import os,base64
from pathlib import Path
from email.mime.text import MIMEText

SCOPES=["https://www.googleapis.com/auth/gmail.modify","https://www.googleapis.com/auth/gmail.compose"]

def _google_imports():
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        return Credentials,InstalledAppFlow,Request,build
    except ImportError as e:
        raise RuntimeError("Connexion Gmail non disponible : installez les dépendances Google de requirements.txt") from e

def _paths():
    cred=Path(os.getenv("GMAIL_CREDENTIALS_FILE","config/google_client_secret.json"));token=Path(os.getenv("GMAIL_TOKEN_FILE","data/gmail/token.json"));token.parent.mkdir(parents=True,exist_ok=True);return cred,token

def service(interactive=True):
    Credentials,InstalledAppFlow,Request,build=_google_imports()
    cred_file,token_file=_paths();creds=None
    if token_file.exists():creds=Credentials.from_authorized_user_file(str(token_file),SCOPES)
    if creds and creds.expired and creds.refresh_token:creds.refresh(Request())
    if not creds or not creds.valid:
        if not interactive:return None
        if not cred_file.exists():raise FileNotFoundError(f"OAuth client absent: {cred_file}")
        flow=InstalledAppFlow.from_client_secrets_file(str(cred_file),SCOPES);creds=flow.run_local_server(port=0);token_file.write_text(creds.to_json(),encoding="utf-8")
    return build("gmail","v1",credentials=creds)

def import_attachments(destination,query=None,max_messages=50):
    svc=service();dest=Path(destination);dest.mkdir(parents=True,exist_ok=True);query=query or os.getenv("GMAIL_QUERY","has:attachment newer_than:30d")
    res=svc.users().messages().list(userId="me",q=query,maxResults=max_messages).execute();saved=[]
    for item in res.get("messages",[]):
        msg=svc.users().messages().get(userId="me",id=item["id"],format="full").execute();stack=[msg.get("payload",{})]
        while stack:
            part=stack.pop();stack.extend(part.get("parts",[]));fn=part.get("filename") or ""
            if Path(fn).suffix.lower() not in {".pdf",".xml",".ubl",".cii"}:continue
            body=part.get("body",{});data=body.get("data")
            if not data and body.get("attachmentId"):data=svc.users().messages().attachments().get(userId="me",messageId=item["id"],id=body["attachmentId"]).execute().get("data")
            if data:
                path=dest/Path(fn).name;path.write_bytes(base64.urlsafe_b64decode(data.encode()));saved.append(str(path))
    return saved

def create_draft(to,subject,body):
    svc=service();msg=MIMEText(body,"plain","utf-8");msg["to"]=to;msg["subject"]=subject;raw=base64.urlsafe_b64encode(msg.as_bytes()).decode();return svc.users().drafts().create(userId="me",body={"message":{"raw":raw}}).execute()

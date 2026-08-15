import os,requests
class ApprovedPlatformConnector:
    def __init__(self):
        self.base=(os.getenv("PA_BASE_URL") or "").rstrip("/")
        self.token=os.getenv("PA_API_TOKEN")
    def configured(self):return bool(self.base and self.token)
    def send(self,payload,document_path=None):
        if not self.configured():return {"ok":False,"error":"Plateforme agréée non configurée"}
        return {"ok":False,"error":"Endpoint fournisseur à configurer dans l'adaptateur spécifique"}

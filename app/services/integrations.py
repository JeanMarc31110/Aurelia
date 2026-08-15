import os
from app.connectors.accounting import PennylaneConnector,SageConnector,CegidConnector
from app.connectors.platform_adapter import ApprovedPlatformConnector

def status():
    return {
      "gmail":{"credentials_file":os.getenv("GMAIL_CREDENTIALS_FILE","config/google_client_secret.json"),
               "configured":os.path.exists(os.getenv("GMAIL_CREDENTIALS_FILE","config/google_client_secret.json"))},
      "pennylane":{"configured":PennylaneConnector.from_env().configured()},
      "sage":{"configured":SageConnector.from_env().configured()},
      "cegid":{"configured":CegidConnector.from_env().configured()},
      "approved_platform":{"configured":ApprovedPlatformConnector().configured()},
      "ebp":{"configured":True,"mode":"CSV"}
    }

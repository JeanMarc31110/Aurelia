import os
from app.local_config import load_local_config
from app.connectors.accounting import PennylaneConnector,SageConnector,CegidConnector
from app.connectors.platform_adapter import ApprovedPlatformConnector

def status():
    local=load_local_config().data_dir
    gmail_credentials=os.getenv("GMAIL_CREDENTIALS_FILE",str(local/"config"/"google_client_secret.json"))
    return {
      "gmail":{"credentials_file":gmail_credentials,"configured":os.path.exists(gmail_credentials)},
      "pennylane":{"configured":PennylaneConnector.from_env().configured()},
      "sage":{"configured":SageConnector.from_env().configured()},
      "cegid":{"configured":CegidConnector.from_env().configured()},
      "approved_platform":{"configured":ApprovedPlatformConnector().configured()},
      "ebp":{"configured":True,"mode":"CSV"}
    }

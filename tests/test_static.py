from pathlib import Path
import json
ROOT=Path(__file__).resolve().parents[1]
def test_policy():
    p=json.loads((ROOT/"config"/"policy.json").read_text())
    assert p["version"]=="5.0.0"
    assert p["autonomy"]["execute_payment"] is False

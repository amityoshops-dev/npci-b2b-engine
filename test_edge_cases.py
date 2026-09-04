import requests
import time

BASE_URL = "https://npci-b2b-engine.onrender.com"

print("--- Running Validated Switch Conformance Test ---")

# Valid Transaction
t_valid = {
    "rail": "IMPS",
    "payer_account": "50100234123412",
    "payer_ifsc": "HDFC0000001",
    "payer_bank_code": "HDFC",
    "payee_account": "00040501234567",
    "payee_ifsc": "ICIC0000001",
    "payee_bank_code": "ICIC",
    "amount_inr": 1500.0,
    "client_ref": f"TST-{int(time.time())}",
    "cycle": "C1"
}

r1 = requests.post(f"{BASE_URL}/v1/npci/execute", json=t_valid, timeout=30)
print(f"[Execution Test] HTTP {r1.status_code}")
if r1.status_code == 200:
    print(f"pacs.008 Payload Returned: {r1.json()}")

# Multilateral Settlement
r2 = requests.post(f"{BASE_URL}/v1/npci/settlement/run-cycle/C1", timeout=30)
print(f"[Settlement Test] HTTP {r2.status_code}")
if r2.status_code == 200:
    print(f"camt.053 Settlement Statement: {r2.json()}")

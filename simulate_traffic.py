import time
import random
import requests
from datetime import datetime

BASE_URL = "https://npci-b2b-engine.onrender.com"

BANKS = [
    {"code": "HDFC", "ifsc": "HDFC0000001", "acc": "50100234123412"},
    {"code": "SBIN", "ifsc": "SBIN0000001", "acc": "20100456123489"},
    {"code": "ICIC", "ifsc": "ICIC0000001", "acc": "00040501234567"},
    {"code": "UTIB", "ifsc": "UTIB0000001", "acc": "91802001234567"},
    {"code": "KKBK", "ifsc": "KKBK0000001", "acc": "12345678901234"},
    {"code": "PUNB", "ifsc": "PUNB0000001", "acc": "01230021000123"}
]

RAILS = ["UPI_P2M", "UPI_P2P", "UPI_AUTOPAY", "UPI_LITE", "IMPS", "AEPS", "NETC_FASTAG", "BBPS"]
CYCLES = ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10", "DC1", "DC2"]

def dispatch_transaction():
    payer = random.choice(BANKS)
    payee = random.choice([b for b in BANKS if b["code"] != payer["code"]])
    rail = random.choice(RAILS)
    cycle = random.choice(CYCLES)
    amount = round(random.uniform(50.0, 950.0) if rail == "UPI_LITE" else random.uniform(100.0, 5000.0), 2)
    client_ref = f"REF-{int(time.time()*1000)}-{random.randint(100, 999)}"

    payload = {
        "rail": rail,
        "payer_account": payer["acc"],
        "payer_ifsc": payer["ifsc"],
        "payer_bank_code": payer["code"],
        "payee_account": payee["acc"],
        "payee_ifsc": payee["ifsc"],
        "payee_bank_code": payee["code"],
        "amount_inr": amount,
        "client_ref": client_ref,
        "cycle": cycle
    }
    try:
        res = requests.post(f"{BASE_URL}/v1/npci/execute", json=payload, timeout=25)
        if res.status_code == 200:
            data = res.json()
            ref_id = data.get("instruction_id") or data.get("end_to_end_id") or client_ref
            print(f"[{datetime.now().strftime('%H:%M:%S')}] 200 OK | {rail} | {payer['code']} -> {payee['code']} | INR {amount} | Ref: {ref_id}")
            return cycle
        else:
            print(f"[{datetime.now().strftime('%H:%M:%S')}] DECLINE ({res.status_code}): {res.text}")
    except Exception as e:
        print(f"[{datetime.now().strftime('%H:%M:%S')}] SWITCH CONN ERROR: {e}")
    return None

def trigger_settlement(cycle_id):
    try:
        res = requests.post(f"{BASE_URL}/v1/npci/settlement/run-cycle/{cycle_id}", timeout=25)
        if res.status_code == 200:
            data = res.json()
            print(f"\n>>> Circular 222 Multilateral Clearing Finalized for {cycle_id} | Statements: {len(data) if isinstance(data, list) else 1} <<<\n")
        else:
            print(f"Settlement HTTP {res.status_code}: {res.text}")
    except Exception as e:
        print(f"Settlement call failed: {e}")

if __name__ == '__main__':
    print("Engaging NPCI Traffic Engine -> https://npci-b2b-engine.onrender.com")
    counter = 0
    active_cycle = "C1"
    while True:
        last_cycle = dispatch_transaction()
        if last_cycle:
            active_cycle = last_cycle
        counter += 1
        if counter % 8 == 0:
            trigger_settlement(active_cycle)
        time.sleep(random.uniform(1.2, 2.5))

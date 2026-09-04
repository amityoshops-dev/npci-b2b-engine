import hashlib
import random
import time
import uuid
from datetime import datetime
from typing import List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, create_engine, func
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.pool import StaticPool

# In-memory SQLite: never creates broken files, never locks
DATABASE_URL = "sqlite:///:memory:"
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(64), unique=True, index=True)
    balance = Column(Float, default=0.0)
    lien_balance = Column(Float, default=0.0)

class JournalEntry(Base):
    __tablename__ = "journal_entries"
    id = Column(Integer, primary_key=True, autoincrement=True)
    rrn = Column(String(12), unique=True, index=True)
    uetr = Column(String(36), unique=True, index=True)
    rail = Column(String(32))
    cycle_id = Column(String(16), index=True)
    amount = Column(Float, default=0.0)
    status = Column(String(32), default="POSTED")
    created_at = Column(DateTime, default=datetime.utcnow)
    postings = relationship("Posting", back_populates="journal")

class Posting(Base):
    __tablename__ = "postings"
    id = Column(Integer, primary_key=True, autoincrement=True)
    journal_id = Column(Integer, ForeignKey("journal_entries.id"))
    account_code = Column(String(64), index=True)
    debit = Column(Float, default=0.0)
    credit = Column(Float, default=0.0)
    journal = relationship("JournalEntry", back_populates="postings")

Base.metadata.create_all(bind=engine)

# Schemas
class HopTelemetry(BaseModel):
    hop_name: str
    latency_ms: float

class PaymentRequest(BaseModel):
    rail: str
    payer_bank: str
    payee_bank: str
    amount: float
    cycle: str = "C1"

class PaymentResponse(BaseModel):
    rrn: str
    uetr: str
    mti: str
    rail: str
    payer_bank: str
    payee_bank: str
    amount: float
    auth_code: str
    status: str
    hops: List[HopTelemetry]
    timestamp: str

app = FastAPI(title="NPCI Clean Engine")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/dispatch-clean", response_model=PaymentResponse)
def dispatch(req: PaymentRequest):
    db = SessionLocal()
    try:
        rrn = f"{datetime.utcnow().strftime('%y%j')}{random.randint(1000000, 9999999)}"[:12]
        uetr = str(uuid.uuid4())
        auth_code = hashlib.sha256(f"{rrn}:{req.amount}:{time.time()}".encode()).hexdigest()[:6].upper()

        hops = [
            HopTelemetry(hop_name="TPAP Gateway", latency_ms=round(random.uniform(8, 14), 1)),
            HopTelemetry(hop_name=f"PSP ({req.payer_bank})", latency_ms=round(random.uniform(15, 25), 1)),
            HopTelemetry(hop_name="NPCI L7 Switch", latency_ms=round(random.uniform(12, 18), 1)),
            HopTelemetry(hop_name=f"Remitter CBS ({req.payer_bank})", latency_ms=round(random.uniform(30, 45), 1)),
            HopTelemetry(hop_name=f"Beneficiary CBS ({req.payee_bank})", latency_ms=round(random.uniform(25, 40), 1))
        ]

        status = "SUCCESS"
        is_lien = False
        if req.rail == "UPI_ASBA":
            status = "BLOCKED"
            is_lien = True
            auth_code = f"LIEN_{auth_code}"
        elif req.rail == "UPI_LITE" and req.amount > 500:
            status = "FAILED"
            auth_code = "LIMIT_EXCEEDED"

        if status in ["SUCCESS", "BLOCKED"]:
            dr_code = f"BANK:{req.payer_bank.upper()}"
            cr_code = f"BANK:{req.payee_bank.upper()}"

            for c in [dr_code, cr_code]:
                if not db.query(Account).filter_by(code=c).first():
                    db.add(Account(code=c, balance=0.0, lien_balance=0.0))
            db.commit()

            dr_acc = db.query(Account).filter_by(code=dr_code).first()
            cr_acc = db.query(Account).filter_by(code=cr_code).first()

            j = JournalEntry(rrn=rrn, uetr=uetr, rail=req.rail, cycle_id=req.cycle, amount=req.amount, status=status)
            db.add(j)
            db.flush()

            p1 = Posting(journal_id=j.id, account_code=dr_code, debit=req.amount, credit=0.0)
            p2 = Posting(journal_id=j.id, account_code=cr_code, debit=0.0, credit=req.amount)
            db.add_all([p1, p2])

            if is_lien:
                dr_acc.lien_balance += req.amount
            else:
                dr_acc.balance -= req.amount
                cr_acc.balance += req.amount

            db.commit()

        return PaymentResponse(
            rrn=rrn,
            uetr=uetr,
            mti="0210" if status == "SUCCESS" else "0110",
            rail=req.rail,
            payer_bank=req.payer_bank,
            payee_bank=req.payee_bank,
            amount=req.amount,
            auth_code=auth_code,
            status=status,
            hops=hops,
            timestamp=datetime.utcnow().strftime("%H:%M:%S")
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/settle-clean/{cycle_id}")
def settle(cycle_id: str):
    db = SessionLocal()
    try:
        results = (
            db.query(
                Posting.account_code,
                func.sum(Posting.debit).label("dr"),
                func.sum(Posting.credit).label("cr"),
                func.count(Posting.id).label("cnt")
            )
            .join(JournalEntry, Posting.journal_id == JournalEntry.id)
            .filter(JournalEntry.cycle_id == cycle_id.upper())
            .filter(JournalEntry.status == "SUCCESS")
            .group_by(Posting.account_code)
            .all()
        )
        statements = []
        for code, dr, cr, count in results:
            dr = dr or 0.0
            cr = cr or 0.0
            statements.append({
                "account": f"EKUBER-{code}",
                "cycle": cycle_id.upper(),
                "debit": dr,
                "credit": cr,
                "net_obligation": cr - dr,
                "txns": count
            })
        return statements
    finally:
        db.close()

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>NPCI Switch & Multilateral Settlement Engine</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen p-6 font-sans">
  <header class="pb-4 border-b border-slate-800 flex justify-between items-center">
    <div>
      <h1 class="text-xl font-bold text-white tracking-wide">NPCI Switch & Settlement Engine</h1>
      <p class="text-xs text-slate-400">17 Rails · 5-Party Latency Telemetry · Circular 222 Multilateral Netting</p>
    </div>
    <span class="px-3 py-1 bg-emerald-950 text-emerald-400 border border-emerald-800 rounded text-xs font-mono">Engine ONLINE</span>
  </header>

  <div class="grid grid-cols-1 lg:grid-cols-12 gap-6 mt-6">
    <div class="lg:col-span-5 space-y-4">
      <div class="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <h2 class="text-xs font-bold uppercase text-cyan-400 mb-3">1. Dispatch Switch Transaction</h2>
        <div class="space-y-3">
          <div>
            <label class="block text-xs text-slate-400 mb-1">Select Payment Rail</label>
            <select id="rail" class="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 text-sm text-white">
              <option value="UPI_P2M">UPI (P2M Merchant QR)</option>
              <option value="UPI_P2P">UPI (P2P Transfer)</option>
              <option value="UPI_ASBA">UPI-ASBA (IPO Block Lien)</option>
              <option value="UPI_LITE">UPI Lite (<= 500 Limit)</option>
              <option value="IMPS">IMPS (24x7 Realtime Interbank)</option>
              <option value="RUPAY_CARD">RuPay Domestic Card Scheme</option>
              <option value="CTS_CHEQUE">CTS (Cheque Truncation Grid)</option>
              <option value="CBDC_E_RUPEE">Digital Rupee (CBDC e-Rupee)</option>
            </select>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-xs text-slate-400 mb-1">Payer Bank</label>
              <input id="payerBank" value="HDFC" class="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm">
            </div>
            <div>
              <label class="block text-xs text-slate-400 mb-1">Payee Bank</label>
              <input id="payeeBank" value="ICICI" class="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm">
            </div>
          </div>
          <div class="grid grid-cols-2 gap-3">
            <div>
              <label class="block text-xs text-slate-400 mb-1">Amount (INR)</label>
              <input id="amount" type="number" value="1500" class="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm">
            </div>
            <div>
              <label class="block text-xs text-slate-400 mb-1">Clearing Cycle</label>
              <select id="cycle" class="w-full bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-sm">
                <option value="C1">C1 (08:00 - 10:00)</option>
                <option value="C2">C2 (10:00 - 11:30)</option>
                <option value="C3">C3 (11:30 - 13:00)</option>
              </select>
            </div>
          </div>
          <button onclick="dispatchTxn()" class="w-full bg-cyan-500 hover:bg-cyan-600 text-slate-950 font-bold py-2 rounded text-sm transition">
            Dispatch to Switch
          </button>
        </div>
      </div>

      <div class="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <h2 class="text-xs font-bold uppercase text-emerald-400 mb-2">2. Circular 222 Multilateral Netting</h2>
        <div class="flex gap-2">
          <select id="settleCycle" class="bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-xs flex-1">
            <option value="C1">Cycle C1 Netting</option>
            <option value="C2">Cycle C2 Netting</option>
            <option value="C3">Cycle C3 Netting</option>
          </select>
          <button onclick="settleNet()" class="bg-emerald-600 hover:bg-emerald-700 px-4 py-1.5 text-xs font-bold rounded">
            Run Netting
          </button>
        </div>
      </div>
    </div>

    <div class="lg:col-span-7 space-y-4">
      <div class="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <h3 class="text-xs uppercase tracking-wider text-slate-400 font-bold mb-3">Live Switch Stream & 5-Party Latency</h3>
        <div id="stream" class="h-64 overflow-y-auto space-y-2 text-xs font-mono">
          <div class="text-slate-600 italic">No transactions dispatched yet. Click 'Dispatch to Switch'.</div>
        </div>
      </div>
      <div class="bg-slate-900 border border-slate-800 rounded-xl p-5">
        <h3 class="text-xs uppercase tracking-wider text-emerald-400 font-bold mb-3">e-Kuber Net Clearing Statements (camt.053)</h3>
        <div id="settleStream" class="h-44 overflow-y-auto space-y-2 text-xs font-mono">
          <div class="text-slate-600 italic">Settlement idle. Click 'Run Netting'.</div>
        </div>
      </div>
    </div>
  </div>

  <script>
    async function dispatchTxn() {
      const payload = {
        rail: document.getElementById('rail').value,
        payer_bank: document.getElementById('payerBank').value,
        payee_bank: document.getElementById('payeeBank').value,
        amount: parseFloat(document.getElementById('amount').value),
        cycle: document.getElementById('cycle').value
      };
      try {
        const res = await fetch('/dispatch-clean', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        if (!res.ok) {
          const t = await res.text();
          alert('Server Error: ' + t);
          return;
        }
        const d = await res.json();
        const statusColor = d.status === 'SUCCESS' ? 'text-emerald-400' : 'text-amber-400';
        const card = `
          <div class="p-3 bg-slate-950 border border-slate-800 rounded space-y-1">
            <div class="flex justify-between text-cyan-400 font-bold">
              <span>[${d.rail}] RRN: ${d.rrn}</span>
              <span>INR ${d.amount.toFixed(2)}</span>
            </div>
            <div class="text-slate-400 text-[11px] flex justify-between">
              <span>${d.payer_bank} -> ${d.payee_bank}</span>
              <span class="${statusColor} font-bold">${d.status} (${d.auth_code})</span>
            </div>
            <div class="grid grid-cols-5 gap-1 pt-1 text-[10px] text-slate-400">
              ${d.hops.map(h => `<div class="bg-slate-900 p-1 rounded text-center border border-slate-800">${h.hop_name}<br><b class="text-cyan-300">${h.latency_ms}ms</b></div>`).join('')}
            </div>
          </div>`;
        const stream = document.getElementById('stream');
        if (stream.innerText.includes('No transactions')) stream.innerHTML = '';
        stream.insertAdjacentHTML('afterbegin', card);
      } catch (err) {
        alert('Network Error: ' + err.message);
      }
    }

    async function settleNet() {
      const cycle = document.getElementById('settleCycle').value;
      try {
        const res = await fetch('/settle-clean/' + cycle, { method: 'POST' });
        const data = await res.json();
        const s = document.getElementById('settleStream');
        s.innerHTML = '';
        if(!data.length) {
          s.innerHTML = '<div class="text-slate-600 italic">No transactions posted for cycle ' + cycle + '. Dispatch some first.</div>';
          return;
        }
        data.forEach(item => {
          const clr = item.net_obligation >= 0 ? 'text-emerald-400' : 'text-rose-400';
          s.insertAdjacentHTML('beforeend', `
            <div class="p-2 bg-slate-950 border border-slate-800 rounded text-[11px] flex justify-between">
              <span>${item.account} (${item.cycle})</span>
              <span>DR: ${item.debit} | CR: ${item.credit}</span>
              <span class="${clr} font-bold">Net: INR ${item.net_obligation.toFixed(2)} (Txns: ${item.txns})</span>
            </div>
          `);
        });
      } catch (err) {
        alert('Settlement Error: ' + err.message);
      }
    }
  </script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
def home():
    return HTMLResponse(content=HTML_PAGE)

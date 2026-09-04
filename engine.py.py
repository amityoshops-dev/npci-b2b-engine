import hashlib
import random
import time
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, create_engine, func
from sqlalchemy.orm import declarative_base, relationship, sessionmaker
from sqlalchemy.pool import StaticPool

# In-memory thread-safe SQLite database (Never locks or corrupts)
DATABASE_URL = "sqlite:///:memory:"
db_engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
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
    rail = Column(String(32), index=True)
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

Base.metadata.create_all(bind=db_engine)

class NPCIRail(str, Enum):
    UPI_P2M = "UPI_P2M"
    UPI_P2P = "UPI_P2P"
    UPI_AUTOPAY = "UPI_AUTOPAY"
    UPI_LITE = "UPI_LITE"
    UPI_ASBA = "UPI_ASBA"
    UPI_123PAY = "UPI_123PAY"
    UPI_INTL = "UPI_INTL"
    CREDIT_ON_UPI = "CREDIT_ON_UPI"
    IMPS = "IMPS"
    AEPS = "AEPS"
    NACH = "NACH"
    NETC_FASTAG = "NETC_FASTAG"
    BBPS = "BBPS"
    NFS_ATM = "NFS_ATM"
    RUPAY_CARD = "RUPAY_CARD"
    CBDC_E_RUPEE = "CBDC_E_RUPEE"
    CTS_CHEQUE = "CTS_CHEQUE"

class HopTelemetry(BaseModel):
    hop_name: str
    latency_ms: float

class PaymentRequest(BaseModel):
    rail: NPCIRail
    payer_bank: str
    payee_bank: str
    amount: float = Field(gt=0)
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

class SettlementStatement(BaseModel):
    account: str
    cycle: str
    debit: float
    credit: float
    net_obligation: float
    txns: int

app = FastAPI(title="NPCI Enterprise Switch & Circular 222 Engine", version="5.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/api/dispatch", response_model=PaymentResponse)
def dispatch_switch_txn(req: PaymentRequest):
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        julian = now.strftime("%y%j")
        seq = str(random.randint(100000, 999999))
        rrn = f"{julian}{seq}"[:12]
        uetr = str(uuid.uuid4())
        auth_code = hashlib.sha256(f"{rrn}:{req.amount}:{time.time()}".encode()).hexdigest()[:6].upper()

        hops = [
            HopTelemetry(hop_name="1. App Gateway", latency_ms=round(random.uniform(6.0, 11.0), 1)),
            HopTelemetry(hop_name=f"2. PSP ({req.payer_bank})", latency_ms=round(random.uniform(12.0, 20.0), 1)),
            HopTelemetry(hop_name="3. NPCI L7 Switch", latency_ms=round(random.uniform(8.0, 15.0), 1)),
            HopTelemetry(hop_name=f"4. Remitter CBS ({req.payer_bank})", latency_ms=round(random.uniform(25.0, 38.0), 1)),
            HopTelemetry(hop_name=f"5. Beneficiary CBS ({req.payee_bank})", latency_ms=round(random.uniform(18.0, 32.0), 1))
        ]

        status = "SUCCESS"
        is_lien = False

        if req.rail == NPCIRail.UPI_ASBA:
            status = "LIEN_BLOCKED"
            is_lien = True
            auth_code = f"LIEN_{auth_code}"
        elif req.rail == NPCIRail.UPI_LITE and req.amount > 1000:
            status = "DECLINED_EXCEEDS_LITE_LIMIT"
            auth_code = "LIMIT_EXCEEDED"

        if status in ["SUCCESS", "LIEN_BLOCKED"]:
            dr_code = f"BANK:{req.payer_bank.upper()}"
            cr_code = f"BANK:{req.payee_bank.upper()}"

            for code in [dr_code, cr_code]:
                if not db.query(Account).filter_by(code=code).first():
                    db.add(Account(code=code))
            db.commit()

            dr_acc = db.query(Account).filter_by(code=dr_code).first()
            cr_acc = db.query(Account).filter_by(code=cr_code).first()

            journal = JournalEntry(
                rrn=rrn,
                uetr=uetr,
                rail=req.rail.value,
                cycle_id=req.cycle.upper(),
                amount=req.amount,
                status=status
            )
            db.add(journal)
            db.flush()

            p_dr = Posting(journal_id=journal.id, account_code=dr_code, debit=req.amount, credit=0.0)
            p_cr = Posting(journal_id=journal.id, account_code=cr_code, debit=0.0, credit=req.amount)
            db.add_all([p_dr, p_cr])

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
            rail=req.rail.value,
            payer_bank=req.payer_bank,
            payee_bank=req.payee_bank,
            amount=req.amount,
            auth_code=auth_code,
            status=status,
            hops=hops,
            timestamp=now.strftime("%H:%M:%S")
        )
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()

@app.post("/api/settlement/{cycle_id}", response_model=List[SettlementStatement])
def run_settlement(cycle_id: str):
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
            statements.append(SettlementStatement(
                account=f"EKUBER-{code}",
                cycle=cycle_id.upper(),
                debit=dr,
                credit=cr,
                net_obligation=cr - dr,
                txns=count
            ))
        return statements
    finally:
        db.close()

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>NPCI Enterprise Architecture & Multilateral Settlement Switch</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    body { font-family: 'Plus Jakarta Sans', sans-serif; }
    .font-mono { font-family: 'JetBrains Mono', monospace; }
    
    @keyframes pulse-flow {
      0% { stroke-dashoffset: 24; }
      100% { stroke-dashoffset: 0; }
    }
    .flow-active {
      stroke: #0284c7 !important;
      stroke-width: 3 !important;
      stroke-dasharray: 6 4 !important;
      animation: pulse-flow 0.5s linear infinite !important;
    }
  </style>
</head>
<body class="bg-[#f8fafc] text-[#0f172a] min-h-screen p-4 md:p-8">

  <div class="max-w-7xl mx-auto space-y-6">

    <!-- Header Banner -->
    <header class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm flex flex-col md:flex-row justify-between md:items-center gap-4">
      <div>
        <div class="flex items-center gap-2 mb-1.5">
          <span class="inline-block w-2.5 h-2.5 bg-teal-500 rounded-full"></span>
          <span class="text-xs uppercase tracking-widest font-mono font-bold text-sky-600">Enterprise Fintech Architecture &bull; 17 Live Rails</span>
        </div>
        <h1 class="text-2xl md:text-3xl font-extrabold text-slate-900 tracking-tight">
          NPCI Multi-Rail Switch &amp; <span class="text-teal-600">Circular 222 Settlement Engine</span>
        </h1>
        <p class="text-xs md:text-sm text-slate-500 mt-1">
          Interactive Layer-7 Load Balancer, Topology Reconfiguration, and Multilateral Net Clearing at RBI e-Kuber.
        </p>
      </div>

      <div class="flex items-center gap-3">
        <div class="px-3.5 py-2 bg-emerald-50 border border-emerald-200 text-emerald-800 text-xs font-mono font-semibold rounded-xl flex items-center gap-2 shadow-sm">
          <span class="w-2 h-2 bg-emerald-500 rounded-full animate-ping"></span>
          <span>RBI e-Kuber: ONLINE</span>
        </div>
        <div class="px-3.5 py-2 bg-sky-50 border border-sky-200 text-sky-800 text-xs font-mono font-semibold rounded-xl shadow-sm">
          <span>Circular 222 Compliant</span>
        </div>
      </div>
    </header>

    <!-- Rail Selector Controller -->
    <div class="bg-white p-4 rounded-2xl border border-slate-200 shadow-sm flex flex-col sm:flex-row items-center justify-between gap-4">
      <div class="flex items-center gap-3 w-full sm:w-auto">
        <span class="text-xs font-extrabold uppercase tracking-wider text-slate-500">Active Rail:</span>
        <select id="railSelector" onchange="onRailChange(this.value)" class="bg-slate-50 border border-slate-300 rounded-xl px-4 py-2 text-xs font-bold text-slate-900 focus:outline-none focus:border-sky-500 flex-1 sm:w-80 shadow-inner">
        </select>
      </div>
      <div class="text-[11px] font-mono text-slate-600 flex flex-wrap items-center gap-5">
        <span>Protocol: <b id="railProtocolBadge" class="text-sky-600 font-bold">REST / ISO 20022 pain.001</b></span>
        <span>Clearing Engine: <b id="railReconBadge" class="text-teal-600 font-bold">Circular 222 Netting</b></span>
        <span>Cut-Off Windows: <b class="text-amber-600 font-bold">C1–C10 &amp; DC1/DC2</b></span>
      </div>
    </div>

    <!-- Interactive Visual Architecture SVG Blueprint -->
    <section class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm overflow-hidden">
      <div class="flex items-center justify-between border-b border-slate-100 pb-3 mb-4">
        <h2 class="text-xs uppercase tracking-wider font-extrabold text-slate-800 flex items-center gap-2">
          <svg class="w-4 h-4 text-sky-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 5a1 1 0 011-1h14a1 1 0 011 1v2a1 1 0 01-1 1H5a1 1 0 01-1-1V5zM4 13a1 1 0 011-1h6a1 1 0 011 1v6a1 1 0 01-1 1H5a1 1 0 01-1-1v-6zM16 13a1 1 0 011-1h2a1 1 0 011 1v6a1 1 0 01-1 1h-2a1 1 0 01-1-1v-6z"/></svg>
          Interactive Switch Topology &amp; Request Flow
        </h2>
        <span class="text-[11px] font-mono text-slate-400">Daylight Edition Topology Engine</span>
      </div>

      <div class="w-full overflow-x-auto">
        <svg id="architectureSvg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1440 980" width="100%" height="auto" class="min-w-[1024px] max-w-full rounded-xl">
          <defs>
            <filter id="shadow" x="-2%" y="-2%" width="104%" height="106%" filterUnits="userSpaceOnUse">
              <feDropShadow dx="0" dy="2" stdDeviation="3" flood-color="#0f172a" flood-opacity="0.08"/>
            </filter>
            <marker id="arrow" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 1 L 10 5 L 0 9 z" fill="#0284c7"/>
            </marker>
            <marker id="arrow-back" viewBox="0 0 10 10" refX="6" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 1 L 10 5 L 0 9 z" fill="#64748b"/>
            </marker>
          </defs>

          <!-- Outer Border -->
          <rect x="10" y="10" width="1420" height="960" rx="14" fill="#ffffff" stroke="#e2e8f0" stroke-width="1.5"/>

          <!-- Title Banner -->
          <text id="svgTitle" x="40" y="50" font-size="20" font-weight="800" fill="#0f172a">UPI (P2M Merchant QR) — High Level Architecture &amp; Switch Request Flow</text>
          <text id="svgSubtitle" x="40" y="72" font-size="12" font-style="italic" fill="#64748b">Central Switch Validation, Dual CBS Confirmation, and Intraday Multilateral Settlement Finality</text>

          <!-- Right: Capabilities Panel -->
          <g transform="translate(1090, 35)">
            <rect x="0" y="0" width="315" height="335" rx="10" fill="#0f2b5c" filter="url(#shadow)"/>
            <text x="18" y="28" font-size="13" font-weight="700" fill="#ffffff">Switch Engine Operations</text>
            <g font-size="10.5" fill="#e2e8f0" transform="translate(18, 52)">
              <text x="0" y="0">✔  TLS 1.3 Termination &amp; ISO Parsing</text>
              <text x="0" y="24">✔  Request Validation (Schema &amp; Signatures)</text>
              <text x="0" y="48">✔  Routing (IFSC, Directory, Bank Health)</text>
              <text x="0" y="72">✔  Traffic Management (Queuing &amp; TPS)</text>
              <text x="0" y="96">✔  Zero PII Data Persistence at Switch</text>
              <text x="0" y="120">✔  HSM MPIN / Token Decryption</text>
              <text x="0" y="144">✔  5-Party CBS Latency &amp; Network Tracing</text>
              <text x="0" y="168">✔  Circular 222 Multilateral Netting (C1–C10)</text>
              <text x="0" y="192">✔  URCS Dispute &amp; Auto-Reversal Queue</text>
              <text x="0" y="216">✔  e-Kuber RTGS Batch Delivery (camt.053)</text>
            </g>

            <!-- Rules Panel -->
            <rect x="0" y="350" width="315" height="185" rx="10" fill="#f8fafc" stroke="#cbd5e1" stroke-width="1.5"/>
            <text x="18" y="375" font-size="12" font-weight="700" fill="#0f2b5c">Active Rail Operating Rules</text>
            <g font-size="10" fill="#334155" transform="translate(18, 398)">
              <text id="rule1" x="0" y="0">• Dynamic/Static QR payload verification.</text>
              <text id="rule2" x="0" y="20">• Funds reside exclusively in bank CBS.</text>
              <text id="rule3" x="0" y="40">• RRN is immutable across all server hops.</text>
              <text id="rule4" x="0" y="60">• Multilateral Netting satisfies zero-sum proof:</text>
              <text x="12" y="78" font-family="monospace" font-weight="700" fill="#0284c7">∑(Net Bank Obligations) = 0</text>
              <text id="rule5" x="0" y="98">• PSS Act 2007 §23 finality protection.</text>
              <text id="rule6" x="0" y="118">• C1–C10 Auth cycles isolated from DC1/DC2.</text>
            </g>
          </g>

          <!-- Left: Payer Entity -->
          <g transform="translate(40, 100)">
            <rect x="0" y="0" width="125" height="235" rx="10" fill="#ecfdf5" stroke="#a7f3d0" stroke-width="1.5"/>
            <text x="26" y="26" font-size="11" font-weight="700" fill="#065f46" letter-spacing="0.5">PAYER SIDE</text>
            <rect x="15" y="48" width="95" height="70" rx="8" fill="#ffffff" stroke="#cbd5e1"/>
            <text id="payerBoxTitle" x="25" y="78" font-size="11" font-weight="600" fill="#0f172a">Payer App</text>
            <text id="payerBoxSub" x="20" y="98" font-size="9" fill="#64748b">PhonePe/GPay</text>
            <circle cx="62" cy="170" r="17" fill="#e2e8f0"/>
            <text id="payerIcon" x="62" y="175" font-size="12" text-anchor="middle" fill="#475569">👤</text>
            <text id="payerEntity" x="62" y="206" font-size="10" font-weight="600" text-anchor="middle" fill="#1e293b">Customer / Payer</text>
          </g>

          <path d="M 165 210 L 255 210" stroke="#0284c7" stroke-width="2" marker-end="url(#arrow)"/>
          <text id="payerToSwitchMsg" x="210" y="200" font-size="9.5" font-weight="600" text-anchor="middle" fill="#0284c7">pain.001</text>

          <!-- Center: Central Switch Infrastructure -->
          <g transform="translate(255, 90)">
            <rect x="0" y="0" width="665" height="280" rx="12" fill="#f8fafc" stroke="#94a3b8" stroke-width="1.5" filter="url(#shadow)"/>
            <rect x="0" y="0" width="665" height="32" rx="12" fill="#0f2b5c"/>
            <rect x="0" y="16" width="665" height="16" fill="#0f2b5c"/>
            <text x="332" y="21" font-size="11.5" font-weight="700" fill="#ffffff" text-anchor="middle">NPCI SWITCH INFRASTRUCTURE (Layer 7 Load Balancer / Switch)</text>

            <g transform="translate(15, 45)">
              <rect x="0" y="0" width="116" height="110" rx="8" fill="#ffffff" stroke="#cbd5e1"/>
              <text x="58" y="20" font-size="10" font-weight="700" text-anchor="middle" fill="#0f172a">L7 Gateway</text>
              <text x="58" y="44" font-size="8.5" text-anchor="middle" fill="#475569">TLS 1.3 Termination</text>
              <text x="58" y="58" font-size="8.5" text-anchor="middle" fill="#475569">XML/REST Parsing</text>
              <text x="58" y="72" font-size="8.5" text-anchor="middle" fill="#475569">Common Library (CL)</text>
              <text x="58" y="86" font-size="8.5" text-anchor="middle" fill="#0284c7">HSM Decryption</text>

              <rect x="130" y="0" width="116" height="110" rx="8" fill="#ffffff" stroke="#cbd5e1"/>
              <text x="188" y="20" font-size="10" font-weight="700" text-anchor="middle" fill="#0f172a">Validation Layer</text>
              <text x="188" y="44" font-size="8.5" text-anchor="middle" fill="#475569">VPA / Handle Syntax</text>
              <text x="188" y="58" font-size="8.5" text-anchor="middle" fill="#475569">Mandate Tokenization</text>
              <text x="188" y="72" font-size="8.5" text-anchor="middle" fill="#475569">UPI Lite (≤₹1000)</text>
              <text x="188" y="86" font-size="8.5" text-anchor="middle" fill="#0284c7">UPI-ASBA Lien Flag</text>

              <rect x="260" y="0" width="116" height="110" rx="8" fill="#ffffff" stroke="#cbd5e1"/>
              <text x="318" y="20" font-size="10" font-weight="700" text-anchor="middle" fill="#0f172a">Routing Engine</text>
              <text x="318" y="44" font-size="8.5" text-anchor="middle" fill="#475569">Bank Routing Directory</text>
              <text x="318" y="58" font-size="8.5" text-anchor="middle" fill="#475569">NIPL Cross-Border</text>
              <text x="318" y="72" font-size="8.5" text-anchor="middle" fill="#475569">CTS Cheque Grid</text>
              <text x="318" y="86" font-size="8.5" text-anchor="middle" fill="#0284c7">ISO 8583 Packaging</text>

              <rect x="390" y="0" width="116" height="110" rx="8" fill="#ffffff" stroke="#cbd5e1"/>
              <text x="448" y="20" font-size="10" font-weight="700" text-anchor="middle" fill="#0f172a">Risk &amp; Control</text>
              <text x="448" y="44" font-size="8.5" text-anchor="middle" fill="#475569">Adaptive Throttling</text>
              <text x="448" y="58" font-size="8.5" text-anchor="middle" fill="#475569">Velocity Rate Limiter</text>
              <text x="448" y="72" font-size="8.5" text-anchor="middle" fill="#475569">CBS Health Monitor</text>
              <text x="448" y="86" font-size="8.5" text-anchor="middle" fill="#0284c7">Auto-Reversal Queue</text>

              <rect x="520" y="0" width="116" height="110" rx="8" fill="#ffffff" stroke="#cbd5e1"/>
              <text x="578" y="20" font-size="10" font-weight="700" text-anchor="middle" fill="#0f172a">Ledger &amp; Clearing</text>
              <text x="578" y="44" font-size="8.5" text-anchor="middle" fill="#475569">Double-Entry Journal</text>
              <text x="578" y="58" font-size="8.5" text-anchor="middle" fill="#475569">Circular 222 Engine</text>
              <text x="578" y="72" font-size="8.5" text-anchor="middle" fill="#475569">10 Daily Cycles (C1-10)</text>
              <text x="578" y="86" font-size="8.5" text-anchor="middle" fill="#0284c7">URCS Dispute Clearing</text>
            </g>

            <rect x="15" y="168" width="635" height="52" rx="6" fill="#f1f5f9" stroke="#cbd5e1"/>
            <text x="332" y="186" font-size="9.5" font-weight="700" fill="#334155" text-anchor="middle">Non-PII Ephemeral In-Memory Stores &amp; Metrics</text>
            <text x="332" y="204" font-size="8.5" fill="#64748b" text-anchor="middle">RRN Indices | ISO 8583 MTI Timestamps | CBS Latency Telemetry | Zero Account Storage</text>

            <rect x="15" y="232" width="635" height="28" rx="6" fill="#0f2b5c"/>
            <text x="332" y="250" font-size="9.5" font-weight="600" fill="#f8fafc" text-anchor="middle">Active-Active Dual Data Centers (Mumbai &amp; Hyderabad) &bull; Sub-50ms Routing Core</text>
          </g>

          <path d="M 920 210 L 965 210" stroke="#0284c7" stroke-width="2" marker-end="url(#arrow)"/>
          <text id="switchToPayeeMsg" x="942" y="200" font-size="9.5" font-weight="600" text-anchor="middle" fill="#0284c7">pacs.008</text>

          <!-- Right: Payee Entity -->
          <g transform="translate(965, 100)">
            <rect x="0" y="0" width="125" height="235" rx="10" fill="#ecfdf5" stroke="#a7f3d0" stroke-width="1.5"/>
            <text x="26" y="26" font-size="11" font-weight="700" fill="#065f46" letter-spacing="0.5">PAYEE SIDE</text>
            <rect x="15" y="48" width="95" height="70" rx="8" fill="#ffffff" stroke="#cbd5e1"/>
            <text id="payeeBoxTitle" x="24" y="78" font-size="11" font-weight="600" fill="#0f172a">Payee Bank</text>
            <text id="payeeBoxSub" x="25" y="98" font-size="9" fill="#64748b">ICICI/HDFC</text>
            <circle cx="62" cy="170" r="17" fill="#e2e8f0"/>
            <text id="payeeIcon" x="62" y="175" font-size="12" text-anchor="middle" fill="#475569">🏪</text>
            <text id="payeeEntity" x="62" y="206" font-size="10" font-weight="600" text-anchor="middle" fill="#1e293b">Merchant / Payee</text>
          </g>

          <!-- Lower Sequence Swimlane -->
          <g transform="translate(35, 415)">
            <rect x="0" y="0" width="1370" height="535" rx="12" fill="#ffffff" stroke="#cbd5e1" stroke-width="1.5"/>
            <rect x="0" y="0" width="1370" height="34" rx="12" fill="#0f2b5c"/>
            <rect x="0" y="17" width="1370" height="17" fill="#0f2b5c"/>
            <text id="swimlaneTitle" x="20" y="23" font-size="12" font-weight="700" fill="#ffffff">UPI (P2M) — Multilateral Switch Sequence &amp; Netting Protocol (Circular 222)</text>

            <g transform="translate(20, 48)">
              <rect x="0" y="0" width="180" height="32" rx="6" fill="#f1f5f9" stroke="#94a3b8"/>
              <text id="lane1Label" x="90" y="20" font-size="10.5" font-weight="700" text-anchor="middle" fill="#0f2b5c">Payer App (PhonePe)</text>
              <line x1="90" y1="32" x2="90" y2="470" stroke="#cbd5e1" stroke-dasharray="4"/>

              <rect x="230" y="0" width="180" height="32" rx="6" fill="#f1f5f9" stroke="#94a3b8"/>
              <text id="lane2Label" x="320" y="20" font-size="10.5" font-weight="700" text-anchor="middle" fill="#0f2b5c">Payer PSP (Axis)</text>
              <line x1="320" y1="32" x2="320" y2="470" stroke="#cbd5e1" stroke-dasharray="4"/>

              <rect x="460" y="0" width="220" height="32" rx="6" fill="#0f2b5c"/>
              <text x="570" y="20" font-size="10.5" font-weight="700" text-anchor="middle" fill="#ffffff">NPCI Central Switch Hub</text>
              <line x1="570" y1="32" x2="570" y2="470" stroke="#0284c7" stroke-width="1.5" stroke-dasharray="4"/>

              <rect x="730" y="0" width="180" height="32" rx="6" fill="#f1f5f9" stroke="#94a3b8"/>
              <text id="lane4Label" x="820" y="20" font-size="10.5" font-weight="700" text-anchor="middle" fill="#0f2b5c">Remitter CBS (HDFC)</text>
              <line x1="820" y1="32" x2="820" y2="470" stroke="#cbd5e1" stroke-dasharray="4"/>

              <rect x="950" y="0" width="180" height="32" rx="6" fill="#f1f5f9" stroke="#94a3b8"/>
              <text id="lane5Label" x="1040" y="20" font-size="10.5" font-weight="700" text-anchor="middle" fill="#0f2b5c">Beneficiary CBS (ICICI)</text>
              <line x1="1040" y1="32" x2="1040" y2="470" stroke="#cbd5e1" stroke-dasharray="4"/>

              <rect x="1170" y="0" width="170" height="32" rx="6" fill="#ecfdf5" stroke="#10b981"/>
              <text id="lane6Label" x="1255" y="20" font-size="10.5" font-weight="700" text-anchor="middle" fill="#065f46">RBI e-Kuber (RTGS)</text>
              <line x1="1255" y1="32" x2="1255" y2="470" stroke="#10b981" stroke-dasharray="4"/>
            </g>

            <g transform="translate(20, 98)">
              <line id="seq1" x1="90" y1="20" x2="320" y2="20" stroke="#0284c7" stroke-width="1.5" marker-end="url(#arrow)"/>
              <text id="seqText1" x="205" y="14" font-size="9" font-weight="600" text-anchor="middle" fill="#0369a1">1. ReqPay [pain.001 Initiation + Encrypted MPIN]</text>

              <line id="seq2" x1="320" y1="50" x2="570" y2="50" stroke="#0284c7" stroke-width="1.5" marker-end="url(#arrow)"/>
              <text id="seqText2" x="445" y="44" font-size="9" font-weight="600" text-anchor="middle" fill="#0369a1">2. TLS Termination &amp; Parse XML/ISO Payload</text>

              <rect x="510" y="70" width="120" height="24" rx="4" fill="#e0f2fe" stroke="#0284c7"/>
              <text id="seqTextRrn" x="570" y="85" font-size="8.5" font-weight="700" text-anchor="middle" fill="#0369a1">GENERATE 12-DIGIT RRN</text>

              <line id="seq3" x1="570" y1="115" x2="820" y2="115" stroke="#0284c7" stroke-width="1.5" marker-end="url(#arrow)"/>
              <text id="seqText3" x="695" y="108" font-size="9" font-weight="600" text-anchor="middle" fill="#0369a1">3. Check Balance / Hold Lien (ISO 8583 MTI 0200)</text>

              <line id="seq4" x1="820" y1="145" x2="570" y2="145" stroke="#64748b" stroke-width="1.5" stroke-dasharray="3" marker-end="url(#arrow-back)"/>
              <text id="seqText4" x="695" y="138" font-size="9" font-weight="600" text-anchor="middle" fill="#475569">4. 200 OK (Remitter Account Debited/Held)</text>

              <line id="seq5" x1="570" y1="180" x2="1040" y2="180" stroke="#0284c7" stroke-width="1.5" marker-end="url(#arrow)"/>
              <text id="seqText5" x="805" y="173" font-size="9" font-weight="600" text-anchor="middle" fill="#0369a1">5. Credit Advice (ISO pacs.008 Transfer)</text>

              <line id="seq6" x1="1040" y1="210" x2="570" y2="210" stroke="#64748b" stroke-width="1.5" stroke-dasharray="3" marker-end="url(#arrow-back)"/>
              <text id="seqText6" x="805" y="203" font-size="9" font-weight="600" text-anchor="middle" fill="#475569">6. 200 OK (Beneficiary Account Credited)</text>

              <line id="seq7" x1="570" y1="240" x2="90" y2="240" stroke="#16a34a" stroke-width="1.8" marker-end="url(#arrow)"/>
              <text id="seqText7" x="330" y="233" font-size="9" font-weight="700" text-anchor="middle" fill="#15803d">7. RespPay (Payment Confirmed to User &amp; Merchant)</text>

              <rect x="475" y="260" width="190" height="30" rx="4" fill="#f0fdf4" stroke="#16a34a"/>
              <text x="570" y="275" font-size="8.5" font-weight="700" text-anchor="middle" fill="#166534">ATOMIC DOUBLE-ENTRY COMMIT</text>
              <text id="seqEntryLedger" x="570" y="286" font-size="7.5" text-anchor="middle" fill="#15803d">DR: BANK:HDFC | CR: BANK:ICICI</text>

              <rect x="60" y="315" width="1220" height="24" rx="4" fill="#fffbeb" stroke="#f59e0b"/>
              <text id="seqCutoffBanner" x="670" y="331" font-size="10" font-weight="700" text-anchor="middle" fill="#b45309">
                CIRCULAR 222 CUT-OFF WINDOW (Cycles C1 through C10 &amp; Dispute Cycles DC1 / DC2)
              </text>

              <line id="seq8" x1="570" y1="375" x2="1255" y2="375" stroke="#16a34a" stroke-width="2" marker-end="url(#arrow)"/>
              <text id="seqText8" x="912" y="367" font-size="9" font-weight="700" text-anchor="middle" fill="#15803d">
                8. Post Multilateral Batch Net (Net Clearing Obligations to Reserve Accounts)
              </text>

              <line id="seq9" x1="1255" y1="415" x2="570" y2="415" stroke="#475569" stroke-width="1.5" stroke-dasharray="3" marker-end="url(#arrow-back)"/>
              <text id="seqText9" x="912" y="407" font-size="9" font-weight="600" text-anchor="middle" fill="#334155">
                9. Settle Reserve Accounts &amp; Issue ISO 20022 camt.053 Electronic Statement
              </text>
            </g>
          </g>
        </svg>
      </div>
    </section>

    <!-- Workspace Controls & Live Switch Operations -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">

      <!-- Dynamic Product Specs Card & 12-Cycle Schedule -->
      <section class="lg:col-span-7 space-y-6">
        <div class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
          <div class="flex items-center justify-between border-b border-slate-100 pb-3 mb-4">
            <h2 class="text-xs uppercase tracking-wider font-extrabold text-slate-800 flex items-center gap-2">
              <svg class="w-4 h-4 text-teal-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10"/></svg>
              Live Architecture Specification (Active Rail)
            </h2>
            <span id="activeRailTag" class="text-[11px] font-mono bg-sky-50 text-sky-700 px-3 py-1 rounded-lg border border-sky-200 font-bold">UPI_P2M</span>
          </div>
          <div id="productDetailCard" class="bg-slate-50 border border-slate-200 rounded-xl p-4 text-xs space-y-3">
          </div>
        </div>

        <div class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
          <div class="flex items-center justify-between border-b border-slate-100 pb-3 mb-4">
            <h2 class="text-xs uppercase tracking-wider font-extrabold text-slate-800 flex items-center gap-2">
              <svg class="w-4 h-4 text-amber-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
              Circular 222 Clearing Framework (All 12 Cycles)
            </h2>
            <span class="text-[11px] font-mono text-slate-400">10 Auth + 2 Dispute Windows</span>
          </div>
          <div class="overflow-x-auto">
            <table class="w-full text-[11px] font-mono border border-slate-200 rounded-xl overflow-hidden">
              <thead>
                <tr class="bg-slate-100 text-slate-800 text-left">
                  <th class="p-2 border-b border-slate-200">Cycle</th>
                  <th class="p-2 border-b border-slate-200">Cut-Off Window</th>
                  <th class="p-2 border-b border-slate-200">Scope &amp; Traffic Type</th>
                  <th class="p-2 border-b border-slate-200">Settlement Engine</th>
                </tr>
              </thead>
              <tbody class="divide-y divide-slate-200 text-slate-600">
                <tr class="hover:bg-slate-50"><td class="p-2 font-bold text-sky-600">C1</td><td class="p-2">08:00 &ndash; 10:00</td><td class="p-2">Morning peak authorization batch</td><td class="p-2">e-Kuber RTGS</td></tr>
                <tr class="hover:bg-slate-50"><td class="p-2 font-bold text-sky-600">C2</td><td class="p-2">10:00 &ndash; 11:30</td><td class="p-2">Mid-morning clearing batch</td><td class="p-2">e-Kuber RTGS</td></tr>
                <tr class="hover:bg-slate-50"><td class="p-2 font-bold text-sky-600">C3</td><td class="p-2">11:30 &ndash; 13:00</td><td class="p-2">Noon clearing batch</td><td class="p-2">e-Kuber RTGS</td></tr>
                <tr class="hover:bg-slate-50"><td class="p-2 font-bold text-sky-600">C4</td><td class="p-2">13:00 &ndash; 14:30</td><td class="p-2">Post-lunch clearing batch</td><td class="p-2">e-Kuber RTGS</td></tr>
                <tr class="hover:bg-slate-50"><td class="p-2 font-bold text-sky-600">C5</td><td class="p-2">14:30 &ndash; 16:00</td><td class="p-2">Afternoon commercial batch</td><td class="p-2">e-Kuber RTGS</td></tr>
                <tr class="hover:bg-slate-50"><td class="p-2 font-bold text-sky-600">C6</td><td class="p-2">16:00 &ndash; 17:30</td><td class="p-2">Evening market clearing batch</td><td class="p-2">e-Kuber RTGS</td></tr>
                <tr class="hover:bg-slate-50"><td class="p-2 font-bold text-sky-600">C7</td><td class="p-2">17:30 &ndash; 19:00</td><td class="p-2">Retail evening batch</td><td class="p-2">e-Kuber RTGS</td></tr>
                <tr class="hover:bg-slate-50"><td class="p-2 font-bold text-sky-600">C8</td><td class="p-2">19:00 &ndash; 20:30</td><td class="p-2">Dinner rush peak batch</td><td class="p-2">e-Kuber RTGS</td></tr>
                <tr class="hover:bg-slate-50"><td class="p-2 font-bold text-sky-600">C9</td><td class="p-2">20:30 &ndash; 22:00</td><td class="p-2">Late evening retail batch</td><td class="p-2">e-Kuber RTGS</td></tr>
                <tr class="hover:bg-slate-50"><td class="p-2 font-bold text-sky-600">C10</td><td class="p-2">22:00 &ndash; 23:59</td><td class="p-2">End-of-day final authorization cut-off</td><td class="p-2">e-Kuber RTGS</td></tr>
                <tr class="bg-amber-50/50 hover:bg-amber-50"><td class="p-2 font-bold text-amber-600">DC1</td><td class="p-2">00:00 &ndash; 16:00</td><td class="p-2">URCS Online Chargebacks &amp; Reversals Session 1</td><td class="p-2 font-bold text-amber-700">Dispute Netting</td></tr>
                <tr class="bg-amber-50/50 hover:bg-amber-50"><td class="p-2 font-bold text-amber-600">DC2</td><td class="p-2">16:00 &ndash; 24:00</td><td class="p-2">URCS Online Chargebacks &amp; Reversals Session 2</td><td class="p-2 font-bold text-amber-700">Dispute Netting</td></tr>
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <!-- Dispatcher & Live Netting Cockpit -->
      <aside class="lg:col-span-5 space-y-6">

        <div class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
          <h3 class="text-xs font-extrabold uppercase tracking-wider text-sky-600 mb-4 flex items-center gap-2">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/></svg>
            Trigger Switch Transaction
          </h3>

          <div class="space-y-3.5 text-xs">
            <div class="grid grid-cols-2 gap-2.5">
              <div>
                <label class="block text-slate-500 font-semibold mb-1">Payer Bank</label>
                <select id="payerBank" class="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-slate-900 font-mono">
                  <option value="HDFC">HDFC Bank</option>
                  <option value="SBI">State Bank of India</option>
                  <option value="AXIS">Axis Bank</option>
                </select>
              </div>
              <div>
                <label class="block text-slate-500 font-semibold mb-1">Payee Bank</label>
                <select id="payeeBank" class="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-slate-900 font-mono">
                  <option value="ICICI">ICICI Bank</option>
                  <option value="KOTAK">Kotak Mahindra</option>
                  <option value="PNB">Punjab National Bank</option>
                </select>
              </div>
            </div>

            <div class="grid grid-cols-2 gap-2.5">
              <div>
                <label class="block text-slate-500 font-semibold mb-1">Amount (&#8377; INR)</label>
                <input id="amountInput" type="number" value="2500" class="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-slate-900 font-mono">
              </div>
              <div>
                <label class="block text-slate-500 font-semibold mb-1">Target Cycle</label>
                <select id="cycleSelect" class="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-slate-900 font-mono">
                  <option value="C1">C1 (08:00&ndash;10:00)</option>
                  <option value="C2">C2 (10:00&ndash;11:30)</option>
                  <option value="C3">C3 (11:30&ndash;13:00)</option>
                  <option value="C4">C4 (13:00&ndash;14:30)</option>
                  <option value="C5">C5 (14:30&ndash;16:00)</option>
                  <option value="C6">C6 (16:00&ndash;17:30)</option>
                  <option value="C7">C7 (17:30&ndash;19:00)</option>
                  <option value="C8">C8 (19:00&ndash;20:30)</option>
                  <option value="C9">C9 (20:30&ndash;22:00)</option>
                  <option value="C10">C10 (22:00&ndash;23:59)</option>
                  <option value="DC1">DC1 (Disputes 00:00&ndash;16:00)</option>
                  <option value="DC2">DC2 (Disputes 16:00&ndash;24:00)</option>
                </select>
              </div>
            </div>

            <button onclick="dispatchLiveTxn()" class="w-full mt-2 bg-sky-600 hover:bg-sky-500 text-white font-bold py-3 rounded-xl transition-all shadow-md flex items-center justify-center gap-2">
              <span>Dispatch to Switch</span>
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M14 5l7 7m0 0l-7 7m7-7H3"/></svg>
            </button>
          </div>
        </div>

        <div class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
          <div class="flex justify-between items-center mb-3">
            <h3 class="text-xs font-extrabold uppercase tracking-wider text-slate-800">Live Switch Telemetry</h3>
            <span id="txnCountBadge" class="text-[11px] font-mono font-bold text-sky-600 bg-sky-50 px-2.5 py-0.5 rounded-lg border border-sky-100">0 txns</span>
          </div>
          <div id="telemetryFeed" class="h-60 overflow-y-auto space-y-2.5 text-xs font-mono pr-1">
            <div class="text-slate-400 italic text-center p-6 bg-slate-50 rounded-xl border border-slate-100">
              Switch idle. Click 'Dispatch to Switch' above.
            </div>
          </div>
        </div>

        <div class="bg-white border border-slate-200 rounded-2xl p-6 shadow-sm">
          <h3 class="text-xs font-extrabold uppercase tracking-wider text-teal-600 mb-2 flex items-center gap-2">
            <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z"/></svg>
            Circular 222 Multilateral Netting
          </h3>
          <p class="text-[11px] text-slate-500 mb-3">Calculate net clearing obligations across member banks for e-Kuber posting.</p>
          <div class="flex gap-2 mb-3">
            <select id="nettingCycleSelect" class="bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 text-xs text-slate-900 font-mono flex-1">
              <option value="C1">Run C1 Netting</option>
              <option value="C2">Run C2 Netting</option>
              <option value="C3">Run C3 Netting</option>
              <option value="C4">Run C4 Netting</option>
              <option value="C5">Run C5 Netting</option>
              <option value="C6">Run C6 Netting</option>
              <option value="C7">Run C7 Netting</option>
              <option value="C8">Run C8 Netting</option>
              <option value="C9">Run C9 Netting</option>
              <option value="C10">Run C10 Netting</option>
              <option value="DC1">Run DC1 (Dispute Settlement)</option>
              <option value="DC2">Run DC2 (Dispute Settlement)</option>
            </select>
            <button onclick="runNettingMath()" class="bg-teal-600 hover:bg-teal-500 text-white font-bold text-xs px-5 py-2 rounded-xl transition-all shadow-md">
              Run Netting
            </button>
          </div>
          <div id="nettingResults" class="h-44 overflow-y-auto space-y-2 text-[11px] font-mono bg-slate-50 p-3 rounded-xl border border-slate-200">
            <div class="text-slate-400 italic text-center p-3">No settlement executed yet.</div>
          </div>
        </div>

      </aside>

    </div>

  </div>

  <script>
    const railsCatalog = {
      UPI_P2M: {
        code: "UPI_P2M",
        name: "1. UPI (P2M Merchant QR)",
        protocol: "REST / ISO 20022 pain.001",
        recon: "Circular 222 (C1-C10) via e-Kuber",
        svgTitle: "UPI (P2M Merchant QR) — Architecture & Request Flow",
        svgSubtitle: "Dynamic QR Intent Verification, HSM MPIN Validation & Real-time Merchant Credit",
        payerTitle: "Payer App", payerSub: "PhonePe/GPay", payerIcon: "📱", payerEntity: "Customer",
        payeeTitle: "Merchant POS", payeeSub: "Dynamic QR", payeeIcon: "🏪", payeeEntity: "Merchant",
        payerMsg: "pain.001 (ReqPay)", switchMsg: "pacs.008 (Credit Advice)",
        seq1: "1. ReqPay [pain.001 Intent + Encrypted MPIN]",
        seq2: "2. TLS Termination & Parse VPA / Signature",
        seq3: "3. Check Balance / Hold Funds (ISO MTI 0200)",
        seq4: "4. 200 OK (Remitter Account Debited)",
        seq5: "5. Credit Advice (ISO pacs.008 Transfer)",
        seq6: "6. 200 OK (Merchant Account Credited)",
        seq7: "7. RespPay (Instant Confirmation Push)",
        rules: ["Dynamic/Static QR payload verification", "Funds reside in RBI-regulated CBS", "RRN immutable across all hops", "∑(Net Bank Obligations) = 0", "PSS Act 2007 §23 finality protection", "Auth cycles isolated from Disputes"],
        flow: "Customer scans merchant dynamic QR -> PSP signs request -> Switch validates VPA -> Remitter CBS debits account -> Beneficiary CBS credits merchant.",
        risk: "Low. Intent payloads signed with cryptographic hashes prevent MITM tampering."
      },
      UPI_P2P: {
        code: "UPI_P2P",
        name: "2. UPI (P2P Interbank Transfer)",
        protocol: "REST / ISO 20022 pain.001",
        recon: "Circular 222 Netting Cycles",
        svgTitle: "UPI (P2P Interbank Transfer) — Architecture Flow",
        svgSubtitle: "Virtual Payment Address Resolution & Instant Account-to-Account Credit",
        payerTitle: "Remitter App", payerSub: "BHIM / Cred", payerIcon: "👤", payerEntity: "Payer",
        payeeTitle: "Beneficiary App", payeeSub: "UPI Handle", payeeIcon: "👤", payeeEntity: "Payee",
        payerMsg: "pain.001 (ReqPay)", switchMsg: "pacs.008 (Credit Advice)",
        seq1: "1. ReqPay [P2P Transfer to VPA/Mobile]",
        seq2: "2. Resolve VPA Handle & Bank Routing Table",
        seq3: "3. Remitter CBS Balance Hold (MTI 0200)",
        seq4: "4. 200 OK (Payer Account Debited)",
        seq5: "5. Beneficiary CBS Credit (pacs.008)",
        seq6: "6. 200 OK (Payee Account Credited)",
        seq7: "7. RespPay (Both Parties Notified via Push)",
        rules: ["VPA mapped to Account & IFSC", "Daily P2P transaction caps enforced", "Immutable RRN generation", "Zero-sum net settlement proof", "Irrevocable finality upon cycle close", "Chargebacks handled via URCS in DC1/2"],
        flow: "Remitter selects contact/VPA -> Switch resolves mapper directory -> Dual CBS legs executed in parallel.",
        risk: "Medium. Social engineering and mistyped VPA routing."
      },
      UPI_AUTOPAY: {
        code: "UPI_AUTOPAY",
        name: "3. UPI AutoPay (Recurring Mandates)",
        protocol: "XML / e-Mandate Engine",
        recon: "Intraday Matching Cycle",
        svgTitle: "UPI AutoPay — Recurring Mandate Architecture",
        svgSubtitle: "Automated Pre-Debit Notification (24h) & Silent Standing Instruction Execution",
        payerTitle: "Mandate App", payerSub: "SIP/OTT Sub", payerIcon: "🔄", payerEntity: "Subscriber",
        payeeTitle: "Biller Corporate", payeeSub: "Merchant Hub", payeeIcon: "🏢", payeeEntity: "Biller",
        payerMsg: "ReqMandate (Auth)", switchMsg: "pacs.008 (SI Debit)",
        seq1: "1. Mandate Trigger [Standing Instruction UMN]",
        seq2: "2. Validate Mandate Token & Pre-Debit 24h Window",
        seq3: "3. Automated Debit Hold on Remitter CBS",
        seq4: "4. 200 OK (Mandate Amount Debited)",
        seq5: "5. Credit Corporate Pool Account (pacs.008)",
        seq6: "6. 200 OK (Merchant Account Credited)",
        seq7: "7. RespDebit (Subscription Invoice Settled)",
        rules: ["Mandate cap enforcement (up to ₹1 Lakh without OTP for MF/SIP)", "24-hour pre-debit push notice mandatory", "Unique Mandate Number (UMN) tracking", "Settles in active intraday cycle", "Revocation available via payer app", "Disputes route to DC1 clearing"],
        flow: "Pre-registered UMN triggers -> Switch checks pre-debit compliance -> Headless debit on customer account.",
        risk: "Low. Mandatory 24h pre-debit notice allows subscriber pause/cancellation."
      },
      UPI_LITE: {
        code: "UPI_LITE",
        name: "4. UPI Lite (On-Device Shadow Ledger)",
        protocol: "Local OS Keystore / REST",
        recon: "Shadow Wallet Batch Netting",
        svgTitle: "UPI Lite — On-Device Shadow Ledger Architecture",
        svgSubtitle: "Near-Zero Latency Small Value Transactions (≤ ₹1,000) Without Hitting Remitter CBS",
        payerTitle: "Lite Wallet", payerSub: "On-Device Keystore", payerIcon: "⚡", payerEntity: "Consumer",
        payeeTitle: "Merchant POS", payeeSub: "QR Receiver", payeeIcon: "🏪", payeeEntity: "Merchant",
        payerMsg: "Offline Intent", switchMsg: "pacs.008 (Batch Advice)",
        seq1: "1. Device Local Balance Deduct (Max ₹1,000)",
        seq2: "2. Dispatch Lite Transit Token to Switch",
        seq3: "3. Bypass Remitter CBS (Pre-Funded Escrow Used)",
        seq4: "4. Internal Escrow Validation OK",
        seq5: "5. Credit Beneficiary CBS in Real-Time",
        seq6: "6. 200 OK (Merchant Account Credited)",
        seq7: "7. RespPay (Sub-100ms Total Experience)",
        rules: ["Transaction limit capped at ₹1,000", "Total wallet balance capped at ₹2,000", "Zero core CBS latency on debit leg", "Escrow pool account maintains collateral", "Settlement posted in batch cycles", "Reconciliation against device counters"],
        flow: "Local device deducts pre-funded wallet balance -> Switch credits merchant directly from bank's pre-funded escrow.",
        risk: "Low. Financial liability bounded by strictly enforced ₹1,000 single transaction cap."
      },
      UPI_ASBA: {
        code: "UPI_ASBA",
        name: "5. UPI-ASBA (Primary Market IPO Lien)",
        protocol: "ISO pain.001 / SEBI Block",
        recon: "Lien Release / Registrar Batch",
        svgTitle: "UPI-ASBA — Primary Market IPO Block Architecture",
        svgSubtitle: "Investor Funds Retained in Savings Account with Real-Time Lien Hold Until Allotment",
        payerTitle: "Investor App", payerSub: "Broker / Demat", payerIcon: "📈", payerEntity: "Investor",
        payeeTitle: "Sponsor Bank", payeeSub: "Escrow IPO", payeeIcon: "🏛️", payeeEntity: "Registrar",
        payerMsg: "ReqBlock (Lien)", switchMsg: "BlockAck (Lien Set)",
        seq1: "1. ReqBlock [IPO Bid Amount Application]",
        seq2: "2. Parse Application Number & Depository ID",
        seq3: "3. Remitter CBS Lien Hold (NO FUNDS MOVED)",
        seq4: "4. 200 OK (Funds Marked with LIEN_HOLD)",
        seq5: "5. Notify Stock Exchange & Registrar (SCSB)",
        seq6: "6. 200 OK (Application Block Confirmed)",
        seq7: "7. RespBlock (Lien Active, Investor Earns Interest)",
        rules: ["Funds remain in investor savings account", "Interest continues accruing during IPO bidding", "Debit happens ONLY upon share allotment", "Unblock initiated immediately for non-allottees", "SEBI-mandated 2-day settlement cycle", "Direct integration with Registrar & Transfer Agents"],
        flow: "IPO bid submitted -> Bank marks lien balance hold -> Funds debited only if shares allotted, else unblocked.",
        risk: "Low. Zero interbank fund movement occurs during the bidding phase."
      },
      UPI_123PAY: {
        code: "UPI_123PAY",
        name: "6. UPI 123PAY (IVR / Sound-Wave)",
        protocol: "DTMF Tone / Dual Tone Multi-Freq",
        recon: "Circular 222 Standard Net",
        svgTitle: "UPI 123PAY — Feature Phone Offline Architecture",
        svgSubtitle: "Interactive Voice Response (IVR) & Encrypted Soundwave Communication",
        payerTitle: "Feature Phone", payerSub: "IVR / Voice", payerIcon: "📞", payerEntity: "Caller",
        payeeTitle: "Merchant", payeeSub: "Tone Receiver", payeeIcon: "📻", payeeEntity: "Shopkeeper",
        payerMsg: "IVR Dial / Sound", switchMsg: "pacs.008 (Credit Advice)",
        seq1: "1. Inbound Call to UPI IVR Gateway / Sound Beacon",
        seq2: "2. DTMF Tone Parsing & Voice Biometric Prompt",
        seq3: "3. Remitter Core CBS Debit (MTI 0200)",
        seq4: "4. 200 OK (Caller Account Debited)",
        seq5: "5. Beneficiary CBS Credit (pacs.008)",
        seq6: "6. 200 OK (Merchant Account Credited)",
        seq7: "7. IVR Audio Voice Confirmation to Caller",
        rules: ["Works without internet connectivity", "Encrypted DTMF / sound transmission", "2-Factor Auth via secure telecom channel", "Standard Circular 222 settlement", "Multilingual voice support enabled", "Disputes handled via bank helpline"],
        flow: "Caller dials IVR -> Enters merchant identifier and PIN via keypad tones -> Switch processes transfer.",
        risk: "Medium. Audio fidelity in noisy industrial or street environments."
      },
      UPI_INTL: {
        code: "UPI_INTL",
        name: "7. UPI International (NIPL Cross-Border)",
        protocol: "ISO 20022 Cross-Border API",
        recon: "Bilateral Nostro/Vostro Settlement",
        svgTitle: "UPI International — NIPL Bilateral Link Architecture",
        svgSubtitle: "Real-time Cross-Border QR Payment with Integrated FX Engine (PayNow, AANI, Lyra)",
        payerTitle: "Tourist App", payerSub: "Indian UPI App", payerIcon: "✈️", payerEntity: "Indian Tourist",
        payeeTitle: "Foreign Merchant", payeeSub: "PayNow/AANI QR", payeeIcon: "🌐", payeeEntity: "Global Merchant",
        payerMsg: "pain.001 (FX Intent)", switchMsg: "pacs.008 (Foreign Rail)",
        seq1: "1. Scan Global QR (PayNow Singapore / AANI UAE)",
        seq2: "2. NIPL Gateway Terminates & Applies FX Conversion",
        seq3: "3. Indian Remitter CBS Debited in INR",
        seq4: "4. 200 OK (INR Funds Held at Sponsor Bank)",
        seq5: "5. Foreign Partner Switch Credit in Foreign Currency",
        seq6: "6. 200 OK (Merchant Credited in Local Currency)",
        seq7: "7. RespPay (Instant Cross-Border Receipt)",
        rules: ["Dynamic FX rate lock during authorization", "Sanctions screening & AML checks applied", "Bilateral settlement between central banks", "Nostro-Vostro account funding rules", "PSS Act & FEMA regulatory compliance", "Dispute resolution via NIPL bilateral framework"],
        flow: "Scan international QR -> NIPL locks live FX rate -> Debits Indian account in INR, credits global merchant in local currency.",
        risk: "Medium. Currency exchange volatility and cross-border regulatory compliance."
      },
      CREDIT_ON_UPI: {
        code: "CREDIT_ON_UPI",
        name: "8. Credit Lines on UPI",
        protocol: "REST / ISO Credit Drawdown",
        recon: "Circular 222 Bank Treasury",
        svgTitle: "Credit Lines on UPI — Drawdown Switch Architecture",
        svgSubtitle: "Pre-Sanctioned Bank Credit Facility Accessible via Standard UPI QR Scans",
        payerTitle: "Borrower App", payerSub: "Credit Line VPA", payerIcon: "💳", payerEntity: "Borrower",
        payeeTitle: "Merchant QR", payeeSub: "Retail Point", payeeIcon: "🏪", payeeEntity: "Merchant",
        payerMsg: "CreditDrawdown", switchMsg: "pacs.008 (Transfer)",
        seq1: "1. Scan Merchant QR with Credit Line VPA",
        seq2: "2. Validate Sanctioned Limit & Interest Tier",
        seq3: "3. Lending Bank CBS Loan Drawdown",
        seq4: "4. 200 OK (Credit Line Debited / Loan Disbursed)",
        seq5: "5. Merchant Bank CBS Credit (pacs.008)",
        seq6: "6. 200 OK (Merchant Account Credited)",
        seq7: "7. RespPay (Loan Drawdown Receipt Generated)",
        rules: ["Pre-approved credit line linked to UPI ID", "Merchant Category Code (MCC) restrictions applied", "Zero interest grace period rules", "Settlement funded from lending bank treasury", "Circular 222 netting integration", "Delinquencies handled under RBI loan recovery rules"],
        flow: "User scans QR using pre-approved credit line -> Bank issues instant micro-drawdown -> Merchant gets settled.",
        risk: "Medium. Credit default risk and unauthorized micro-overdrafts."
      },
      IMPS: {
        code: "IMPS",
        name: "9. IMPS (Immediate Payment Service 24x7)",
        protocol: "ISO 8583 / AS-2 Sockets",
        recon: "24x7 Continuous Multilateral Net",
        svgTitle: "IMPS — 24x7 Real-time Interbank Switch Architecture",
        svgSubtitle: "High-Value & Account+IFSC Routing Engine Supporting Instant Transfers",
        payerTitle: "NetBanking / App", payerSub: "Corporate / Retail", payerIcon: "💻", payerEntity: "Remitter",
        payeeTitle: "Payee Account", payeeSub: "Beneficiary CBS", payeeIcon: "🏦", payeeEntity: "Beneficiary",
        payerMsg: "ISO MTI 0200", switchMsg: "ISO MTI 0210",
        seq1: "1. Transfer Request [Account + IFSC / MMID]",
        seq2: "2. Switch Sockets Validate Routing Directory",
        seq3: "3. Remitter CBS Core Debit (MTI 0200)",
        seq4: "4. 200 OK (Remitter Balance Debited)",
        seq5: "5. Beneficiary CBS Instant Credit",
        seq6: "6. 200 OK (Beneficiary Account Credited)",
        seq7: "7. MTI 0210 Response (Interbank Confirmation)",
        rules: ["Operates 24x7x365 including bank holidays", "Supports MMID and Account+IFSC modes", "Mandatory real-time credit within 30 seconds", "Circular 222 continuous batch netting", "Transaction caps up to ₹5 Lakh", "URCS dispute resolution integration"],
        flow: "Payer enters IFSC + Account -> Switch routes ISO 8583 packet -> Instant real-time interbank credit.",
        risk: "Low. IFSC directory and strict account number validation."
      },
      AEPS: {
        code: "AEPS",
        name: "10. AePS (Aadhaar Biometric Micro-ATM)",
        protocol: "ISO 8583 / UIDAI Biometric Auth",
        recon: "Sub-Member Sponsor Clearing",
        svgTitle: "AePS — Aadhaar Enabled Biometric Micro-ATM Architecture",
        svgSubtitle: "Financial Inclusion Rail for Rural Micro-ATMs Using Fingerprint & Iris Biometrics",
        payerTitle: "Micro-ATM POS", payerSub: "Biometric Scanner", payerIcon: "👆", payerEntity: "Villager / Payer",
        payeeTitle: "BC Agent", payeeSub: "Cash Dispenser", payeeIcon: "💼", payeeEntity: "Bank Mitra",
        payerMsg: "Biometric Auth", switchMsg: "pacs.008 (Disbursement)",
        seq1: "1. Fingerprint / Iris Captured at Micro-ATM POS",
        seq2: "2. Route to UIDAI CIDR for Biometric Verification",
        seq3: "3. Biometric Match OK -> Route to Bank CBS",
        seq4: "4. 200 OK (Aadhaar-Linked Account Debited)",
        seq5: "5. Business Correspondent Pool Credited",
        seq6: "6. 200 OK (Cash Dispense Authorized)",
        seq7: "7. Physical Cash Handed Over to Beneficiary",
        rules: ["Aadhaar authentication via UIDAI CIDR", "BC agent commission calculation", "Sub-member bank sponsor routing", "Circular 222 netting via sponsor bank", "Daily biometric velocity limits", "Strict geographical terminal binding"],
        flow: "Villager scans fingerprint at Micro-ATM -> UIDAI verifies biometric -> Bank account debited -> BC agent gives cash.",
        risk: "High. Biometric spoofing, silicone clones, and Business Correspondent fraud."
      },
      NACH: {
        code: "NACH",
        name: "11. NACH (Bulk Direct Debit / Credit)",
        protocol: "ACH Batch XML (Bulk Engine)",
        recon: "RBI e-Kuber Session Clearing",
        svgTitle: "NACH — High-Volume Bulk Clearing Switch Architecture",
        svgSubtitle: "High-Volume Direct Debits (SIPs/EMIs) and Direct Credits (Salaries/Subsidies/Dividends)",
        payerTitle: "Corporate File", payerSub: "ERP Bulk Upload", payerIcon: "📁", payerEntity: "Corporate / Govt",
        payeeTitle: "Destination Banks", payeeSub: "Millions of CBS", payeeIcon: "🏛️", payeeEntity: "Beneficiaries",
        payerMsg: "ACH Batch File", switchMsg: "e-Kuber Session Instruction",
        seq1: "1. Ingest Corporate ACH Bulk Batch File (100k+ txns)",
        seq2: "2. Validate Mandate Database & Signature Hashes",
        seq3: "3. Partition Transactions Across Destination Banks",
        seq4: "4. Run Batch Clearing Session at Designated Cut-off",
        seq5: "5. Multi-Million CBS Accounts Debited / Credited",
        seq6: "6. Generate Bank Return & Rejection Reports",
        seq7: "7. Net Settlement Instruction Delivered to e-Kuber",
        rules: ["Session-based clearing (Morning & Afternoon)", "Mandate Registration Reference Number (UMRN)", "Automated return processing for insufficient balance", "Finality posted directly to RBI e-Kuber current accounts", "Statutory handling under Section 25 PSS Act", "Massive parallel batch orchestration"],
        flow: "Corporates submit salary/EMI files -> NPCI validates mandate UMRN registry -> Batches settle on RBI e-Kuber.",
        risk: "Medium. High bounce rates due to insufficient balances on EMI dates."
      },
      NETC_FASTAG: {
        code: "NETC_FASTAG",
        name: "12. NETC FASTag (Toll Plaza Settlement)",
        protocol: "ISO 8583 / ETC RFID Protocols",
        recon: "Toll Acquiring & Issuing Cycles",
        svgTitle: "NETC FASTag — National Toll Plaza Switch Architecture",
        svgSubtitle: "Sub-Second RFID Electronic Toll Collection with Offline Edge Verification",
        payerTitle: "Vehicle OBU", payerSub: "RFID Windshield Tag", payerIcon: "🚗", payerEntity: "Vehicle Owner",
        payeeTitle: "Toll Plaza", payeeSub: "Acquiring Bank", payeeIcon: "🛣️", payeeEntity: "Toll Operator",
        payerMsg: "RFID Tag Read", switchMsg: "pacs.008 (Toll Debit)",
        seq1: "1. RFID Tag Scanned at Toll Boom Barrier (EPC Gen 2)",
        seq2: "2. Plaza Server Checks Local Negative List (Sub-50ms)",
        seq3: "3. Barrier Opens -> Toll Advice Sent to Switch",
        seq4: "4. Switch Resolves Tag ID via Central ETC Mapper",
        seq5: "5. FASTag Issuing Bank Wallet / Account Debited",
        seq6: "6. Toll Concessionaire Acquiring Bank Credited",
        seq7: "7. Toll Session Settled in Daily NETC Clearing Cycle",
        rules: ["Toll barrier opens within 100ms using local cache", "Negative tag list synced continuously across plazas", "ETC Mapper maps Tag ID to Issuing Bank", "Multilateral settlement between Acquirer and Issuer", "Overcharge chargebacks handled via dispute window", "NHAI concessionaire revenue split"],
        flow: "RFID reader reads FASTag -> Barrier opens instantly -> Switch debits wallet and credits toll concessionaire.",
        risk: "Low. Plaza-level negative lists cache blacklist tags locally."
      },
      BBPS: {
        code: "BBPS",
        name: "13. BBPS (Bharat BillPay Operations)",
        protocol: "BBPOU Standard XML / REST",
        recon: "Consolidated Central Biller Net",
        svgTitle: "Bharat BillPay (BBPS) — Central Operations Architecture",
        svgSubtitle: "Unified Utility Bill Presentment, Validation, and Consolidated Payment Settlement",
        payerTitle: "Customer App", payerSub: "Agent Unit (COU)", payerIcon: "💡", payerEntity: "Consumer",
        payeeTitle: "Utility Biller", payeeSub: "Biller Unit (BOU)", payeeIcon: "⚡", payeeEntity: "Electricity/Water",
        payerMsg: "FetchBill / PayBill", switchMsg: "pacs.008 (Biller Credit)",
        seq1: "1. Customer Enters Consumer Number (Fetch Request)",
        seq2: "2. Switch Routes to Central Biller Registry",
        seq3: "3. Biller Operating Unit (BOU) Returns Due Amount",
        seq4: "4. Customer Approves Payment -> Debit Payer Account",
        seq5: "5. Credit Bou Settlement Escrow Account",
        seq6: "6. Real-Time Payment Receipt Emitted to Consumer",
        seq7: "7. Consolidated Multilateral Biller Batch Netting",
        rules: ["Standardized biller category schema", "Interoperable agent institution network", "Instant payment guarantee & electronic receipt", "Central dispute handling mechanism (CMS)", "Daily consolidated settlement via e-Kuber", "Consumer complaint tracking SLA (48h)"],
        flow: "Customer enters consumer ID -> Central registry fetches bill -> Payment debited and credited to utility escrow.",
        risk: "Low. Bill fetch confirms verified liability before debit."
      },
      NFS_ATM: {
        code: "NFS_ATM",
        name: "14. NFS (ATM Interbank Cash Switch)",
        protocol: "ISO 8583 MTI 0200 / 0210",
        recon: "Daily ATM Interchange Netting",
        svgTitle: "NFS — National Financial Switch (ATM Network) Architecture",
        svgSubtitle: "Shared ATM Switch Routing Domestic Cash Withdrawals and Balance Inquiries",
        payerTitle: "Off-Us ATM", payerSub: "Card Reader & POS", payerIcon: "🏧", payerEntity: "ATM User",
        payeeTitle: "Card Issuer Bank", payeeSub: "Issuer Core CBS", payeeIcon: "🏛️", payeeEntity: "Issuing Bank",
        payerMsg: "MTI 0200 (Withdrawal)", switchMsg: "MTI 0210 (Dispense)",
        seq1: "1. Card Inserted & PIN Entered at Off-Us ATM",
        seq2: "2. ISO 8583 MTI 0200 Packet Routed to NFS Switch",
        seq3: "3. Switch Decrypts PIN Block inside HSM",
        seq4: "4. Card-Issuing Bank Debits Cardholder Account",
        seq5: "5. MTI 0210 Response -> ATM Dispenses Physical Cash",
        seq6: "6. ATM Acquiring Bank Calculates Interchange Fee",
        seq7: "7. Daily NFS Multilateral Settlement on e-Kuber",
        rules: ["Strict ISO 8583 standard field alignment", "ATM interchange fee computation", "Hardware cash dispenser sensor verification", "Auto-reversal for cash dispense timeout (MTI 0420)", "Settlement posted in daily clearing batches", "Section 23 finality protection"],
        flow: "Card swiped at off-us ATM -> NFS routes ISO 8583 packet to card issuer -> Cash dispensed upon approval.",
        risk: "Medium. Physical card skimming and cash dispenser sensor timeouts."
      },
      RUPAY_CARD: {
        code: "RUPAY_CARD",
        name: "15. RuPay Domestic Card & Tokenisation",
        protocol: "3DS 2.0 / CoF Token / ISO 8583",
        recon: "RuPay Clearing & Settlement (RCS)",
        svgTitle: "RuPay Domestic Card Scheme & Tokenisation Architecture",
        svgSubtitle: "Card-on-File Tokenization (CoFT) Vault & Dual-Interface POS / E-Commerce Switch",
        payerTitle: "Cardholder Browser", payerSub: "Merchant App CoFT", payerIcon: "💳", payerEntity: "Shopper",
        payeeTitle: "E-Com Merchant", payeeSub: "Payment Gateway", payeeIcon: "🛒", payeeEntity: "Merchant",
        payerMsg: "3DS 2.0 Auth", switchMsg: "pacs.008 (Settlement)",
        seq1: "1. Checkout with Card-on-File Token (CoFT)",
        seq2: "2. Switch Resolves Cryptographic Token to Virtual PAN",
        seq3: "3. 3D-Secure 2.0 OTP Authentication Prompt",
        seq4: "4. Issuing Bank CBS Debits Card Balance (MTI 0200)",
        seq5: "5. Acquiring Bank Escrow Credited (MTI 0210)",
        seq6: "6. Merchant Payment Gateway Confirms Order",
        seq7: "7. RCS Multilateral Netting Batch Dispatched to e-Kuber",
        rules: ["RBI Card-on-File Tokenisation mandate compliance", "Zero actual card numbers stored by merchants", "3DS 2.0 frictionless risk-based authentication", "Domestic clearing fee advantages over Visa/Mastercard", "Chargeback arbitration handled in dispute cycles", "Co-badged international cards use partner networks"],
        flow: "Tokenized PAN submitted -> Switch resolves real PAN in secure vault -> 3DS OTP validates debit.",
        risk: "Medium. Card-not-present e-commerce fraud and chargeback reversals."
      },
      CBDC_E_RUPEE: {
        code: "CBDC_E_RUPEE",
        name: "16. Digital Rupee (CBDC e₹ Sovereign Retail)",
        protocol: "Cryptographic Sovereign Token API",
        recon: "Instant Tokenized Finality",
        svgTitle: "Digital Rupee (CBDC e₹) — Sovereign Retail Architecture",
        svgSubtitle: "Direct Two-Tier Sovereign Token Distribution with Wallet-to-Wallet Instant Settlement",
        payerTitle: "CBDC Wallet", payerSub: "Digital Rupee App", payerIcon: "₹", payerEntity: "Citizen",
        payeeTitle: "Merchant Wallet", payeeSub: "CBDC Terminal", payeeIcon: "🏪", payeeEntity: "Merchant",
        payerMsg: "Token Signature", switchMsg: "Token Transfer Ack",
        seq1: "1. Wallet Signs Cryptographic Digital Sovereign Token",
        seq2: "2. Route to RBI Token Verification Node (2-Tier Architecture)",
        seq3: "3. Authenticate Sovereign Cryptographic Signature",
        seq4: "4. Token Ownership Transferred in Central Ledger",
        seq5: "5. Instant Finality (NO INTERBANK SETTLEMENT NEEDED)",
        seq6: "6. Merchant Wallet Receives Verified e₹ Tokens",
        seq7: "7. Zero Interbank Settlement Risk (Direct Sovereign Obligation)",
        rules: ["Legal tender issued directly by the Reserve Bank of India", "Non-interest-bearing digital sovereign cash", "Zero interbank multilateral clearing needed", "Instant settlement finality upon token handover", "Programmable purpose-bound features enabled", "Supports offline token transfers"],
        flow: "Citizen signs sovereign cryptographic token -> Token ownership transfers on RBI ledger -> Instant finality.",
        risk: "Low. Sovereign liability backed directly by the Reserve Bank of India."
      },
      CTS_CHEQUE: {
        code: "CTS_CHEQUE",
        name: "17. CTS (Cheque Truncation Grid Clearing)",
        protocol: "Image-Based Cheque Clearing (MICR)",
        recon: "Grid Session Settlement (Western/Northern/Southern)",
        svgTitle: "CTS — Cheque Truncation System Grid Clearing Architecture",
        svgSubtitle: "Image-Based Physical Cheque Clearing across Western, Northern, and Southern Grids",
        payerTitle: "Drawee Bank", payerSub: "Payer Branch", payerIcon: "📝", payerEntity: "Drawer",
        payeeTitle: "Presenting Bank", payeeSub: "Deposit Counter", payeeIcon: "🏦", payeeEntity: "Payee",
        payerMsg: "MICR + UV Image", switchMsg: "Grid Clearing Advice",
        seq1: "1. Cheque Deposited at Presenting Bank Branch",
        seq2: "2. Capture Front Grey-Scale, UV, Back Image & MICR Band",
        seq3: "3. Transmit Image Batch to Regional CTS Grid",
        seq4: "4. Drawee Bank Branch Inspects Signature & Funds",
        seq5: "5. Drawee Bank Acknowledges Cheque Clearance",
        seq6: "6. Presenting Bank Credits Payee Customer Account",
        seq7: "7. CTS Multilateral Clearing File Settled on e-Kuber",
        rules: ["Paper cheques truncated at presenting bank branch", "Regional clearing grids (Mumbai, Delhi, Chennai)", "Positive Pay System (PPS) verification for > ₹50,000", "Image quality & digital signature validation", "Statutory clearing under Negotiable Instruments Act", "Returns processed within standard 24h clearing window"],
        flow: "Cheque scanned at branch -> Image & MICR sent to CTS grid -> Drawee bank clears -> e-Kuber settles.",
        risk: "Medium. Signature forgery, physical image tampering, and MICR mismatches."
      }
    };

    const railSelect = document.getElementById('railSelector');
    Object.keys(railsCatalog).forEach(k => {
      const r = railsCatalog[k];
      railSelect.insertAdjacentHTML('beforeend', `<option value="${r.code}">${r.name}</option>`);
    });

    let currentRail = 'UPI_P2M';

    function onRailChange(railCode) {
      currentRail = railCode;
      const r = railsCatalog[railCode];

      document.getElementById('railProtocolBadge').innerText = r.protocol;
      document.getElementById('railReconBadge').innerText = r.recon;
      document.getElementById('activeRailTag').innerText = r.code;

      document.getElementById('svgTitle').textContent = r.svgTitle;
      document.getElementById('svgSubtitle').textContent = r.svgSubtitle;
      document.getElementById('swimlaneTitle').textContent = `${r.name} — Multilateral Sequence & Netting (Circular 222)`;

      document.getElementById('payerBoxTitle').textContent = r.payerTitle;
      document.getElementById('payerBoxSub').textContent = r.payerSub;
      document.getElementById('payerIcon').textContent = r.payerIcon;
      document.getElementById('payerEntity').textContent = r.payerEntity;

      document.getElementById('payeeBoxTitle').textContent = r.payeeTitle;
      document.getElementById('payeeBoxSub').textContent = r.payeeSub;
      document.getElementById('payeeIcon').textContent = r.payeeIcon;
      document.getElementById('payeeEntity').textContent = r.payeeEntity;

      document.getElementById('payerToSwitchMsg').textContent = r.payerMsg;
      document.getElementById('switchToPayeeMsg').textContent = r.switchMsg;

      document.getElementById('seqText1').textContent = r.seq1;
      document.getElementById('seqText2').textContent = r.seq2;
      document.getElementById('seqText3').textContent = r.seq3;
      document.getElementById('seqText4').textContent = r.seq4;
      document.getElementById('seqText5').textContent = r.seq5;
      document.getElementById('seqText6').textContent = r.seq6;
      document.getElementById('seqText7').textContent = r.seq7;

      r.rules.forEach((rule, idx) => {
        const el = document.getElementById(`rule${idx+1}`);
        if (el) el.textContent = `• ${rule}`;
      });

      document.getElementById('productDetailCard').innerHTML = `
        <div class="flex justify-between items-center border-b border-slate-200 pb-2">
          <span class="font-bold text-slate-900 text-sm">${r.name}</span>
          <span class="font-mono text-[10px] bg-sky-50 text-sky-700 px-2.5 py-1 rounded-md border border-sky-200 font-bold">${r.code}</span>
        </div>
        <div>
          <span class="font-bold text-slate-500 block text-[10px] uppercase">Transaction Flow:</span>
          <p class="text-slate-800 mt-0.5">${r.flow}</p>
        </div>
        <div>
          <span class="font-bold text-slate-500 block text-[10px] uppercase">Settlement Mechanism:</span>
          <p class="text-slate-800 mt-0.5">${r.recon}</p>
        </div>
        <div>
          <span class="font-bold text-slate-500 block text-[10px] uppercase">Risk Vector &amp; Mitigation:</span>
          <p class="text-slate-800 mt-0.5">${r.risk}</p>
        </div>
      `;
    }

    onRailChange('UPI_P2M');

    function triggerFlowAnimation() {
      const stepIds = ['seq1', 'seq2', 'seq3', 'seq4', 'seq5', 'seq6', 'seq7', 'seq8', 'seq9'];
      stepIds.forEach((id, idx) => {
        setTimeout(() => {
          const el = document.getElementById(id);
          if (el) {
            el.classList.add('flow-active');
            setTimeout(() => el.classList.remove('flow-active'), 1200);
          }
        }, idx * 160);
      });
    }

    let totalTxns = 0;

    async function dispatchLiveTxn() {
      const rail = currentRail;
      const payer = document.getElementById('payerBank').value;
      const payee = document.getElementById('payeeBank').value;
      const amt = parseFloat(document.getElementById('amountInput').value);
      const cycle = document.getElementById('cycleSelect').value;

      if (payer === payee) {
        alert('Payer Bank and Payee Bank must be different for interbank netting.');
        return;
      }
      if (isNaN(amt) || amt <= 0) {
        alert('Please enter a valid positive payment amount.');
        return;
      }

      triggerFlowAnimation();

      const payload = {
        rail: rail,
        payer_bank: payer,
        payee_bank: payee,
        amount: amt,
        cycle: cycle
      };

      try {
        const res = await fetch('/api/dispatch', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload)
        });
        
        if (!res.ok) {
          const errData = await res.json();
          throw new Error(errData.detail || 'Dispatch failed');
        }
        
        const d = await res.json();
        
        totalTxns++;
        document.getElementById('txnCountBadge').innerText = `${totalTxns} txns`;
        document.getElementById('seqEntryLedger').textContent = `DR: BANK:${d.payer_bank} | CR: BANK:${d.payee_bank}`;

        const feed = document.getElementById('telemetryFeed');
        if (feed.innerText.includes('Switch idle')) feed.innerHTML = '';

        const statusBadge = d.status === "SUCCESS"
          ? `<span class="text-emerald-700 font-bold bg-emerald-50 px-2 py-0.5 rounded border border-emerald-200">SUCCESS (${d.auth_code})</span>`
          : (d.status === "LIEN_BLOCKED" ? `<span class="text-amber-700 font-bold bg-amber-50 px-2 py-0.5 rounded border border-amber-200">LIEN HELD (${d.auth_code})</span>` : `<span class="text-rose-700 font-bold bg-rose-50 px-2 py-0.5 rounded border border-rose-200">${d.status}</span>`);

        const card = `
          <div class="p-3 bg-white border border-slate-200 rounded-xl space-y-1.5 shadow-sm">
            <div class="flex justify-between items-center">
              <span class="text-sky-600 font-bold text-[11px]">[${d.rail}] RRN: ${d.rrn}</span>
              <span class="text-slate-400 text-[10px]">${d.timestamp}</span>
            </div>
            <div class="flex justify-between text-slate-800 font-medium">
              <span>${d.payer_bank} &rarr; ${d.payee_bank}</span>
              <span class="font-bold font-mono text-slate-900">&#8377;${d.amount.toFixed(2)}</span>
            </div>
            <div class="flex justify-between items-center text-[10px]">
              <span class="text-slate-500 font-mono">Cycle: ${cycle}</span>
              ${statusBadge}
            </div>
            <div class="grid grid-cols-5 gap-1 pt-1.5 border-t border-slate-100 text-[9px] text-center">
              ${d.hops.map(h => `<div class="bg-slate-50 p-1 rounded border border-slate-200"><span class="text-slate-500 block truncate">${h.hop_name.split(' ')[1] || h.hop_name}</span><b class="text-sky-600">${h.latency_ms}ms</b></div>`).join('')}
            </div>
          </div>
        `;
        feed.insertAdjacentHTML('afterbegin', card);
      } catch (err) {
        alert('Switch Error: ' + err.message);
      }
    }

    async function runNettingMath() {
      const cycle = document.getElementById('nettingCycleSelect').value;
      const out = document.getElementById('nettingResults');
      out.innerHTML = '';

      try {
        const res = await fetch(`/api/settlement/${cycle}`, { method: 'POST' });
        if (!res.ok) throw new Error('Settlement request failed');
        
        const statements = await res.json();

        if (statements.length === 0) {
          out.innerHTML = `<div class="text-slate-400 italic text-center p-3">No posted transactions found for ${cycle}. Dispatch transactions under this cycle first.</div>`;
          return;
        }

        let netProofSum = 0;
        statements.forEach(item => {
          netProofSum += item.net_obligation;
          const netClass = item.net_obligation >= 0 ? 'text-emerald-700' : 'text-rose-700';
          const action = item.net_obligation >= 0 ? 'e-Kuber Inflow' : 'e-Kuber Outflow';

          out.insertAdjacentHTML('beforeend', `
            <div class="p-2.5 bg-white rounded-xl border border-slate-200 flex justify-between items-center shadow-sm">
              <div>
                <b class="text-slate-900 text-xs">${item.account}</b>
                <span class="text-slate-500 text-[10px] block">DR: &#8377;${item.debit.toFixed(2)} | CR: &#8377;${item.credit.toFixed(2)}</span>
              </div>
              <div class="text-right">
                <span class="${netClass} font-bold font-mono text-xs">Net: ${item.net_obligation >= 0 ? '+' : ''}&#8377;${item.net_obligation.toFixed(2)}</span>
                <span class="text-[9px] text-slate-500 block uppercase font-semibold">${action}</span>
              </div>
            </div>
          `);
        });

        out.insertAdjacentHTML('beforeend', `
          <div class="mt-2 pt-2 border-t border-slate-200 flex justify-between text-[11px] text-slate-800 font-semibold">
            <span>Multilateral Netting Proof:</span>
            <b class="font-mono text-teal-600">&sum; Net = &#8377;${netProofSum.toFixed(2)} (Balanced)</b>
          </div>
        `);
      } catch (err) {
        out.innerHTML = `<div class="text-rose-600 text-center p-3">${err.message}</div>`;
      }
    }
  </script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
def serve_dashboard():
    return HTMLResponse(content=DASHBOARD_HTML)
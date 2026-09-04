from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from app.core.database import Base, engine
import app.core.ledger  # Registers tables with Base
from app.routers import rails

# Create all database tables properly
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="NPCI 17-Rail B2B Engine & Settlement Switch",
    version="2.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rails.router)

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>NPCI Multi-Rail B2B Switch Cockpit</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-slate-950 text-slate-100 min-h-screen font-sans p-6">
  <header class="pb-4 border-b border-slate-800 flex justify-between items-center">
    <div>
      <h1 class="text-xl font-bold text-white">NPCI Switch & B2B Settlement Engine</h1>
      <p class="text-xs text-slate-400">17 Rails · 5-Party Latency Telemetry · Circular 222 Clearing</p>
    </div>
    <span class="px-3 py-1 bg-emerald-950 text-emerald-400 text-xs border border-emerald-800 rounded">Switch ONLINE</span>
  </header>

  <div class="grid grid-cols-1 lg:grid-cols-12 gap-6 mt-6">
    <!-- Controls -->
    <div class="lg:col-span-5 space-y-4">
      <div class="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow">
        <h2 class="text-xs uppercase tracking-wider text-cyan-400 font-bold mb-3">1. Dispatch Transaction</h2>
        <div class="space-y-3">
          <div>
            <label class="block text-xs text-slate-400 mb-1">Select Payment Rail</label>
            <select id="rail" class="w-full bg-slate-950 border border-slate-700 rounded px-3 py-2 text-sm text-white">
              <option value="UPI_P2M">UPI (P2M Merchant QR)</option>
              <option value="UPI_P2P">UPI (P2P Transfer)</option>
              <option value="UPI_ASBA">UPI-ASBA (IPO Block Lien)</option>
              <option value="UPI_LITE">UPI Lite (≤ ₹500)</option>
              <option value="IMPS">IMPS (24x7 Interbank)</option>
              <option value="RUPAY_CARD">RuPay Domestic Card</option>
              <option value="CTS_CHEQUE">CTS (Cheque Truncation)</option>
              <option value="CBDC_E_RUPEE">Digital Rupee (e₹)</option>
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
              <label class="block text-xs text-slate-400 mb-1">Amount (₹)</label>
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

      <!-- Netting Engine -->
      <div class="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow">
        <h2 class="text-xs uppercase tracking-wider text-emerald-400 font-bold mb-2">2. Circular 222 Multilateral Netting</h2>
        <div class="flex gap-2">
          <select id="settleCycle" class="bg-slate-950 border border-slate-700 rounded px-3 py-1.5 text-xs flex-1">
            <option value="C1">Cycle C1 Netting</option>
            <option value="C2">Cycle C2 Netting</option>
            <option value="DC1">Cycle DC1 (Disputes)</option>
          </select>
          <button onclick="settleNet()" class="bg-emerald-600 hover:bg-emerald-700 px-4 py-1.5 text-xs font-bold rounded">
            Run Netting
          </button>
        </div>
      </div>
    </div>

    <!-- Live Output -->
    <div class="lg:col-span-7 space-y-4">
      <div class="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow">
        <h3 class="text-xs uppercase tracking-wider text-slate-400 font-bold mb-3">Live Switch Stream & 5-Party Latency</h3>
        <div id="stream" class="h-64 overflow-y-auto space-y-2 text-xs font-mono">
          <div class="text-slate-600 italic">No transactions dispatched yet.</div>
        </div>
      </div>
      <div class="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow">
        <h3 class="text-xs uppercase tracking-wider text-emerald-400 font-bold mb-3">e-Kuber Net Clearing (camt.053)</h3>
        <div id="settleStream" class="h-44 overflow-y-auto space-y-2 text-xs font-mono">
          <div class="text-slate-600 italic">Settlement idle.</div>
        </div>
      </div>
    </div>
  </div>

  <script>
    async function dispatchTxn() {
      const payload = {
        rail: document.getElementById('rail').value,
        payer_account: '9871100234',
        payer_ifsc: document.getElementById('payerBank').value + '0001234',
        payer_bank_code: document.getElementById('payerBank').value,
        payee_account: '5010048291',
        payee_ifsc: document.getElementById('payeeBank').value + '0005678',
        payee_bank_code: document.getElementById('payeeBank').value,
        amount_inr: parseFloat(document.getElementById('amount').value),
        client_ref: 'REF-' + Math.random().toString(36).substring(7).toUpperCase()
      };
      const cycle = document.getElementById('cycle').value;
      const res = await fetch('/v1/npci/execute?cycle=' + cycle, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      if(!res.ok) {
        const t = await res.text();
        alert('Error: ' + t);
        return;
      }
      const d = await res.json();
      const card = `
        <div class="p-3 bg-slate-950 border border-slate-800 rounded space-y-1">
          <div class="flex justify-between text-cyan-400 font-bold">
            <span>[${d.rail}] RRN: ${d.rrn}</span>
            <span>₹${d.amount}</span>
          </div>
          <div class="text-slate-400 text-[11px] flex justify-between">
            <span>${d.debtor_agent} ➔ ${d.creditor_agent}</span>
            <span class="text-emerald-400">${d.status} (${d.auth_code})</span>
          </div>
          <div class="grid grid-cols-5 gap-1 pt-1 text-[10px] text-slate-500">
            ${d.hops ? d.hops.map(h => `<div class="bg-slate-900 p-1 rounded text-center">${h.latency_ms}ms</div>`).join('') : ''}
          </div>
        </div>`;
      const stream = document.getElementById('stream');
      if (stream.innerText.includes('No transactions')) stream.innerHTML = '';
      stream.insertAdjacentHTML('afterbegin', card);
    }

    async function settleNet() {
      const cycle = document.getElementById('settleCycle').value;
      const res = await fetch('/v1/npci/settlement/run-cycle/' + cycle, { method: 'POST' });
      const data = await res.json();
      const s = document.getElementById('settleStream');
      s.innerHTML = '';
      if(!data.length) { s.innerHTML = '<div class="text-slate-600 italic">No txns for ' + cycle + '</div>'; return; }
      data.forEach(item => {
        s.insertAdjacentHTML('beforeend', `
          <div class="p-2 bg-slate-950 border border-slate-800 rounded text-[11px] flex justify-between">
            <span>${item.settlement_account}</span>
            <span class="${item.net_obligation >= 0 ? 'text-emerald-400' : 'text-rose-400'} font-bold">
              Net: ₹${item.net_obligation} (Txns: ${item.transactions_count})
            </span>
          </div>
        `);
      });
    }
  </script>
</body>
</html>"""

@app.get("/", response_class=HTMLResponse)
def dashboard():
    return HTMLResponse(content=HTML_CONTENT)

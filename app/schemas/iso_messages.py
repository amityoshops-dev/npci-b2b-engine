from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field
import uuid

class NPCIRail(str, Enum):
    UPI_P2P = "UPI_P2P"
    UPI_P2M = "UPI_P2M"
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
    E_RUPI = "E_RUPI"
    CTS_CHEQUE = "CTS_CHEQUE"

class TxnStatus(str, Enum):
    INITIATED = "INITIATED"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    REVERSED = "REVERSED"
    CHARGEBACK_RAISED = "CHARGEBACK_RAISED"
    ARBITRATION_SETTLED = "ARBITRATION_SETTLED"

class ISO8583MTI(str, Enum):
    AUTH_REQUEST = "0100"
    AUTH_RESPONSE = "0110"
    FINANCIAL_REQUEST = "0200"
    FINANCIAL_RESPONSE = "0210"
    REVERSAL_REQUEST = "0400"
    REVERSAL_RESPONSE = "0410"
    CHARGEBACK_REQUEST = "0420"
    CHARGEBACK_RESPONSE = "0430"

class HopTelemetry(BaseModel):
    hop_name: str
    in_timestamp: str
    out_timestamp: str
    latency_ms: float
    status: str

class Pain001PaymentInstruction(BaseModel):
    msg_id: str = Field(default_factory=lambda: f"MSG-IN-{uuid.uuid4().hex[:12].upper()}")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    rail: NPCIRail
    payer_vpa: Optional[str] = None
    payer_account: str
    payer_ifsc: str
    payer_bank_code: str
    payee_vpa: Optional[str] = None
    payee_account: str
    payee_ifsc: str
    payee_bank_code: str
    amount_inr: float = Field(gt=0)
    client_ref: str
    webhook_url: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

class Pacs008InterbankTransfer(BaseModel):
    end_to_end_id: str
    rrn: str
    uetr: str
    mti: ISO8583MTI
    rail: NPCIRail
    debtor_agent: str
    creditor_agent: str
    amount: float
    currency: str = "INR"
    auth_code: str
    status: TxnStatus
    npci_transit_timestamp: datetime = Field(default_factory=datetime.utcnow)
    hops: List[HopTelemetry] = Field(default_factory=list)

class DisputeRequest(BaseModel):
    rrn: str
    reason_code: str = "U001_CUSTOMER_DISPUTE_DEBITED_NOT_CREDITED"
    cycle_id: str = "DC1"

class DisputeResponse(BaseModel):
    dispute_id: str
    rrn: str
    status: TxnStatus
    action_taken: str
    settled_in_cycle: str
    reversal_amount: float

class Camt053Statement(BaseModel):
    statement_id: str
    clearing_cycle: str
    settlement_account: str
    net_debit_amount: float
    net_credit_amount: float
    net_obligation: float
    transactions_count: int
    settled_at: datetime

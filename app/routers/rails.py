from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy.orm import Session
from app.core.database import SessionLocal
from app.core.circular222_settlement import Circular222SettlementEngine
from app.core.ledger import DoubleEntryEngine
from app.core.switch import NPCISwitchRouter
from app.core.webhooks import B2BWebhookDispatcher
from app.schemas.iso_messages import (
    Camt053Statement,
    DisputeRequest,
    DisputeResponse,
    Pacs008InterbankTransfer,
    Pain001PaymentInstruction,
    TxnStatus
)

router = APIRouter(prefix="/v1/npci", tags=["NPCI Multi-Rail Gateway"])
switch = NPCISwitchRouter()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@router.post("/execute", response_model=Pacs008InterbankTransfer)
def process_rail_transaction(
    instruction: Pain001PaymentInstruction,
    cycle: str = "C1",
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db)
):
    ledger = DoubleEntryEngine(db)
    pacs = switch.route_and_execute(instruction)
    
    if pacs.status in [TxnStatus.SUCCESS, TxnStatus.BLOCKED]:
        payer_node = f"BANK:{instruction.payer_bank_code.upper()}"
        payee_node = f"BANK:{instruction.payee_bank_code.upper()}"
        
        ledger.post_transaction(
            rrn=pacs.rrn,
            uetr=pacs.uetr,
            rail=pacs.rail.value,
            cycle_id=cycle,
            debit_account=payer_node,
            credit_account=payee_node,
            amount=pacs.amount,
            is_lien=(pacs.status == TxnStatus.BLOCKED)
        )

        if instruction.webhook_url:
            dump_data = pacs.model_dump() if hasattr(pacs, "model_dump") else pacs.dict()
            background_tasks.add_task(
                B2BWebhookDispatcher.dispatch,
                instruction.webhook_url,
                dump_data
            )
            
    return pacs

@router.post("/dispute/raise", response_model=DisputeResponse)
def raise_urcs_chargeback(req: DisputeRequest, db: Session = Depends(get_db)):
    ledger = DoubleEntryEngine(db)
    try:
        orig = ledger.reverse_or_chargeback(req.rrn, dispute_cycle=req.cycle_id)
        return DisputeResponse(
            dispute_id=f"DISP-{orig.rrn}-{req.cycle_id}",
            rrn=req.rrn,
            status=TxnStatus.CHARGEBACK_RAISED,
            action_taken="FUNDS_REVERSED_TO_REMITTER_POSTED_TO_DISPUTE_CYCLE",
            settled_in_cycle=req.cycle_id,
            reversal_amount=orig.amount
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.post("/settlement/run-cycle/{cycle_id}", response_model=list[Camt053Statement])
def trigger_settlement_cycle(cycle_id: str, db: Session = Depends(get_db)):
    settlement_engine = Circular222SettlementEngine(db)
    try:
        return settlement_engine.compute_cycle_netting(cycle_id.upper())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

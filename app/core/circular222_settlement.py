from datetime import datetime
from typing import List
from sqlalchemy import func
from app.core.ledger import JournalEntry, Posting
from app.schemas.iso_messages import Camt053Statement

class Circular222SettlementEngine:
    CYCLES = ["C1", "C2", "C3", "C4", "C5", "C6", "C7", "C8", "C9", "C10", "DC1", "DC2"]

    def __init__(self, db_session):
        self.db = db_session

    def compute_cycle_netting(self, cycle_id: str) -> List[Camt053Statement]:
        if cycle_id not in self.CYCLES:
            raise ValueError(f"Invalid cycle {cycle_id}. Must be one of {self.CYCLES}")

        results = (
            self.db.query(
                Posting.account_code,
                func.sum(Posting.debit).label("total_debit"),
                func.sum(Posting.credit).label("total_credit"),
                func.count(Posting.id).label("txn_count")
            )
            .join(JournalEntry, Posting.journal_id == JournalEntry.id)
            .filter(JournalEntry.cycle_id == cycle_id)
            .filter(JournalEntry.status == "POSTED")
            .group_by(Posting.account_code)
            .all()
        )

        statements = []
        for row in results:
            account_code, total_dr, total_cr, count = row[0], row[1] or 0.0, row[2] or 0.0, row[3]
            if account_code.startswith("BANK:"):
                net_obligation = total_cr - total_dr
                stmt = Camt053Statement(
                    statement_id=f"STMT-{cycle_id}-{account_code.replace(':', '-')}-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}",
                    clearing_cycle=cycle_id,
                    settlement_account=f"EKUBER-{account_code}",
                    net_debit_amount=total_dr,
                    net_credit_amount=total_cr,
                    net_obligation=net_obligation,
                    transactions_count=count,
                    settled_at=datetime.utcnow()
                )
                statements.append(stmt)

        return statements

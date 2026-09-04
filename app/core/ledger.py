from datetime import datetime
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship
from app.core.database import Base

class Account(Base):
    __tablename__ = "accounts"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    code = Column(String(64), unique=True, index=True)
    account_type = Column(String(32))
    balance = Column(Float, default=0.0)
    lien_balance = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

class JournalEntry(Base):
    __tablename__ = "journal_entries"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    rrn = Column(String(12), unique=True, index=True)
    uetr = Column(String(36), unique=True, index=True)
    rail = Column(String(32), index=True)
    cycle_id = Column(String(16), index=True)
    status = Column(String(32), default="POSTED")
    amount = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    postings = relationship("Posting", back_populates="journal_entry")

class Posting(Base):
    __tablename__ = "postings"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    journal_id = Column(Integer, ForeignKey("journal_entries.id"))
    account_code = Column(String(64), index=True)
    debit = Column(Float, default=0.0)
    credit = Column(Float, default=0.0)
    
    journal_entry = relationship("JournalEntry", back_populates="postings")

class DoubleEntryEngine:
    def __init__(self, db_session):
        self.db = db_session

    def ensure_account(self, code: str, account_type: str = "ASSET"):
        acc = self.db.query(Account).filter_by(code=code).first()
        if not acc:
            acc = Account(code=code, account_type=account_type, balance=0.0, lien_balance=0.0)
            self.db.add(acc)
            self.db.commit()
            self.db.refresh(acc)
        return acc

    def post_transaction(self, rrn: str, uetr: str, rail: str, cycle_id: str, debit_account: str, credit_account: str, amount: float, is_lien: bool = False) -> JournalEntry:
        if amount <= 0:
            raise ValueError("Transaction amount must be strictly positive")

        acc_dr = self.ensure_account(debit_account)
        acc_cr = self.ensure_account(credit_account)

        journal = JournalEntry(rrn=rrn, uetr=uetr, rail=rail, cycle_id=cycle_id, status="BLOCKED" if is_lien else "POSTED", amount=amount)
        self.db.add(journal)
        self.db.flush()

        posting_dr = Posting(journal_id=journal.id, account_code=debit_account, debit=amount, credit=0.0)
        posting_cr = Posting(journal_id=journal.id, account_code=credit_account, debit=0.0, credit=amount)
        self.db.add_all([posting_dr, posting_cr])

        if is_lien:
            acc_dr.lien_balance += amount
        else:
            acc_dr.balance -= amount
            acc_cr.balance += amount

        self.db.commit()
        return journal

    def reverse_or_chargeback(self, rrn: str, dispute_cycle: str = "DC1") -> JournalEntry:
        orig = self.db.query(JournalEntry).filter_by(rrn=rrn).first()
        if not orig:
            raise ValueError(f"Transaction with RRN {rrn} not found")
        if orig.status in ["REVERSED", "CHARGEBACK_RAISED"]:
            raise ValueError(f"Transaction with RRN {rrn} already resolved/disputed")

        postings = self.db.query(Posting).filter_by(journal_id=orig.id).all()
        dr_acc_code = next(p.account_code for p in postings if p.debit > 0)
        cr_acc_code = next(p.account_code for p in postings if p.credit > 0)

        acc_dr = self.ensure_account(dr_acc_code)
        acc_cr = self.ensure_account(cr_acc_code)

        acc_dr.balance += orig.amount
        acc_cr.balance -= orig.amount
        orig.status = "CHARGEBACK_RAISED"
        orig.cycle_id = dispute_cycle

        rev_journal = JournalEntry(
            rrn=f"R_{orig.rrn[:10]}",
            uetr=f"REV-{orig.uetr}",
            rail=orig.rail,
            cycle_id=dispute_cycle,
            status="POSTED",
            amount=orig.amount
        )
        self.db.add(rev_journal)
        self.db.flush()

        rev_dr = Posting(journal_id=rev_journal.id, account_code=cr_acc_code, debit=orig.amount, credit=0.0)
        rev_cr = Posting(journal_id=rev_journal.id, account_code=dr_acc_code, debit=0.0, credit=orig.amount)
        self.db.add_all([rev_dr, rev_cr])

        self.db.commit()
        return orig

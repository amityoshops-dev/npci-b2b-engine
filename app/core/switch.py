import hashlib
import random
import time
import uuid
from datetime import datetime
from app.schemas.iso_messages import HopTelemetry, ISO8583MTI, NPCIRail, Pacs008InterbankTransfer, Pain001PaymentInstruction, TxnStatus

class NPCISwitchRouter:
    @staticmethod
    def generate_rrn() -> str:
        julian = datetime.utcnow().strftime("%y%j")
        seq = str(random.randint(1000000, 9999999))
        return f"{julian}{seq}"[:12]

    @staticmethod
    def generate_uetr() -> str:
        return str(uuid.uuid4())

    def simulate_telemetry_hops(self, payer_bank: str, payee_bank: str) -> list[HopTelemetry]:
        hops_config = [
            ("TPAP_CLIENT_GATEWAY", 12.4),
            (f"PAYER_PSP_{payer_bank}", 24.1),
            ("NPCI_CORE_L7_SWITCH", 18.3),
            (f"REMITTER_CBS_{payer_bank}", 45.7),
            (f"BENEFICIARY_CBS_{payee_bank}", 38.2)
        ]
        telemetry = []
        for name, lat in hops_config:
            jitter = random.uniform(-4.0, 6.0)
            final_lat = max(2.0, round(lat + jitter, 1))
            telemetry.append(HopTelemetry(
                hop_name=name,
                in_timestamp=datetime.utcnow().strftime("%H:%M:%S.%f")[:-3],
                out_timestamp=datetime.utcnow().strftime("%H:%M:%S.%f")[:-3],
                latency_ms=final_lat,
                status="200_OK"
            ))
        return telemetry

    def route_and_execute(self, instruction: Pain001PaymentInstruction) -> Pacs008InterbankTransfer:
        rrn = self.generate_rrn()
        uetr = self.generate_uetr()
        auth_code = hashlib.sha256(f"{rrn}:{instruction.amount_inr}:{time.time()}".encode()).hexdigest()[:6].upper()
        hops = self.simulate_telemetry_hops(instruction.payer_bank_code.upper(), instruction.payee_bank_code.upper())

        # 1. Rail: UPI Lite Limit enforcement (Max Rs 500)
        if instruction.rail == NPCIRail.UPI_LITE and instruction.amount_inr > 500:
            return Pacs008InterbankTransfer(
                end_to_end_id=instruction.msg_id,
                rrn=rrn,
                uetr=uetr,
                mti=ISO8583MTI.FINANCIAL_RESPONSE,
                rail=instruction.rail,
                debtor_agent=instruction.payer_ifsc,
                creditor_agent=instruction.payee_ifsc,
                amount=instruction.amount_inr,
                auth_code="DECLINED_EXCEEDS_LITE_LIMIT",
                status=TxnStatus.FAILED,
                hops=hops
            )

        # 2. Rail: UPI-ASBA Primary Market Lien Block
        if instruction.rail == NPCIRail.UPI_ASBA:
            return Pacs008InterbankTransfer(
                end_to_end_id=instruction.msg_id,
                rrn=rrn,
                uetr=uetr,
                mti=ISO8583MTI.AUTH_RESPONSE,
                rail=instruction.rail,
                debtor_agent=instruction.payer_ifsc,
                creditor_agent=instruction.payee_ifsc,
                amount=instruction.amount_inr,
                auth_code=f"LIEN_{auth_code}",
                status=TxnStatus.BLOCKED,
                hops=hops
            )

        # 3. Rail: CTS Cheque Truncation Grid Clearing
        if instruction.rail == NPCIRail.CTS_CHEQUE:
            grid = instruction.metadata.get("cts_grid", "WESTERN_GRID_MUMBAI")
            cheque_no = instruction.metadata.get("cheque_no", "104928")
            return Pacs008InterbankTransfer(
                end_to_end_id=instruction.msg_id,
                rrn=rrn,
                uetr=uetr,
                mti=ISO8583MTI.FINANCIAL_RESPONSE,
                rail=instruction.rail,
                debtor_agent=instruction.payer_ifsc,
                creditor_agent=instruction.payee_ifsc,
                amount=instruction.amount_inr,
                auth_code=f"CTS_{grid[:3]}_{cheque_no}",
                status=TxnStatus.SUCCESS,
                hops=hops
            )

        # 4. Standard Digital Rail Execution
        return Pacs008InterbankTransfer(
            end_to_end_id=instruction.msg_id,
            rrn=rrn,
            uetr=uetr,
            mti=ISO8583MTI.FINANCIAL_RESPONSE,
            rail=instruction.rail,
            debtor_agent=instruction.payer_ifsc,
            creditor_agent=instruction.payee_ifsc,
            amount=instruction.amount_inr,
            auth_code=auth_code,
            status=TxnStatus.SUCCESS,
            hops=hops
        )

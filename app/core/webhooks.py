import asyncio
import hashlib
import hmac
import json
import httpx
from datetime import datetime

class B2BWebhookDispatcher:
    SECRET_KEY = "NPCI_SECRET_WEBHOOK_KEY_9921"

    @classmethod
    def generate_signature(cls, payload_dict: dict) -> str:
        body_bytes = json.dumps(payload_dict, sort_keys=True).encode()
        return hmac.new(cls.SECRET_KEY.encode(), body_bytes, hashlib.sha256).hexdigest()

    @classmethod
    async def dispatch(cls, target_url: str, payload_dict: dict):
        if not target_url:
            return
        signature = cls.generate_signature(payload_dict)
        headers = {
            "Content-Type": "application/json",
            "X-NPCI-Signature": signature,
            "X-NPCI-Timestamp": datetime.utcnow().isoformat()
        }
        async with httpx.AsyncClient(timeout=3.0) as client:
            for attempt in range(1, 4):
                try:
                    await client.post(target_url, json=payload_dict, headers=headers)
                    break
                except Exception:
                    await asyncio.sleep(0.5 * attempt)

import asyncio
import json
import urllib.request
from datetime import datetime
from typing import Any, Dict, Optional

from utils.logger import get_logger

logger = get_logger(__name__)


async def send_webhook(event: str, data: Optional[Dict[str, Any]] = None) -> None:
    """Send HTTP POST to configured webhook URL. Silently fails if no URL set."""
    try:
        from utils.settings import get_webhook_url
        url = get_webhook_url()
        if not url:
            return

        payload = {
            "event": event,
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            **(data or {}),
        }

        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        def _post() -> int:
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "TeleClone-Bot/2.0",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                return resp.status

        loop = asyncio.get_event_loop()
        status = await loop.run_in_executor(None, _post)
        logger.debug(f"Webhook '{event}' sent → HTTP {status}")

    except Exception as e:
        logger.debug(f"Webhook '{event}' failed (non-critical): {e}")

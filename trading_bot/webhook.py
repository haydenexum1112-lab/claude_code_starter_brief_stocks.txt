from __future__ import annotations
import logging

import requests

from trading_bot.config import TRADERSPOST_WEBHOOK_URL

logger = logging.getLogger(__name__)


def send(payload: dict) -> bool:
    """POST an approved signal to the TradersPost webhook. Returns True on success."""
    try:
        resp = requests.post(TRADERSPOST_WEBHOOK_URL, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info(f"Webhook OK [{resp.status_code}]: {payload}")
        return True
    except requests.RequestException as exc:
        logger.error(f"Webhook FAILED for {payload.get('ticker', '?')}: {exc}")
        return False

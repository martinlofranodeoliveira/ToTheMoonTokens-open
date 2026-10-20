from __future__ import annotations

import httpx

from .config import get_settings
from .observability import get_logger

log = get_logger(__name__)


def ping_arc_network() -> dict[str, object]:
    settings = get_settings()
    url = settings.arc_testnet_rpc_url

    payload = {"jsonrpc": "2.0", "method": "eth_chainId", "params": [], "id": 1}

    try:
        response = httpx.post(url, json=payload, timeout=5.0)
        response.raise_for_status()
        data = response.json()
        chain_id_hex = data.get("result")
        if chain_id_hex is not None:
            chain_id = int(chain_id_hex, 16)
            return {
                "status": "online",
                "chain_id": chain_id,
                "url": url,
            }
        else:
            return {
                "status": "degraded",
                "error": "Invalid RPC response",
                "url": url,
            }
    except Exception as exc:
        log.warning("arc_network_ping_failed", error=str(exc))
        return {
            "status": "offline",
            "error": str(exc),
            "url": url,
        }

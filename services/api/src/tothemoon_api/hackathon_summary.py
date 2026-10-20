from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter

from .arc import ping_arc_network
from .arc_adapter import get_arc_jobs
from .circle import circle_client
from .config import _ROOT_DIR, get_settings
from .demo_agent import list_demo_jobs
from .guards import connector_status, evaluate_guardrails
from .payments import get_catalog

router = APIRouter(prefix="/api/hackathon", tags=["hackathon"])
_PACKAGED_EVIDENCE_PATH = Path(__file__).with_name("data") / "nanopayments-batch-2026-04-23.json"

_FALLBACK_SUMMARY: dict[str, Any] = {}  # No original operational evidence is distributed.

_FALLBACK_TRANSACTIONS = []  # No original operational evidence is distributed.

_FALLBACK_WALLETS = []  # No original operational evidence is distributed.

_TRACKS = [
    "Agent-to-Agent Payment Loop",
    "Per-API Monetization Engine",
    "Real-Time Micro-Commerce Flow",
]


def _latest_evidence_path() -> Path | None:
    # The open edition never discovers a previous operator's evidence directory.
    return _PACKAGED_EVIDENCE_PATH if _PACKAGED_EVIDENCE_PATH.exists() else None


def _load_latest_evidence() -> tuple[Path | None, dict[str, Any] | None]:
    evidence_path = _latest_evidence_path()
    if evidence_path is None:
        return None, None
    try:
        payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return evidence_path, None
    if not isinstance(payload, dict):
        return evidence_path, None
    return evidence_path, payload


def _wallet_inventory(evidence: dict[str, Any] | None) -> list[dict[str, str]]:
    if circle_client.wallets_loaded and circle_client.wallets_by_role:
        live_wallets: list[dict[str, str]] = []
        for role, wallet in circle_client.wallets_by_role.items():
            name = role.strip().lower()
            address = str(wallet.get("address", "")).strip()
            if name and address:
                live_wallets.append({"name": name, "address": address})
        if live_wallets:
            return live_wallets

    if not evidence:
        return [wallet.copy() for wallet in _FALLBACK_WALLETS]

    wallets: list[dict[str, str]] = []
    source = evidence.get("source")
    if isinstance(source, dict):
        name = str(source.get("name", "")).strip()
        address = str(source.get("address", "")).strip()
        if name and address:
            wallets.append({"name": name, "address": address})

    destinations = evidence.get("destinations")
    if isinstance(destinations, list):
        for item in destinations:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            address = str(item.get("address", "")).strip()
            if name and address:
                wallets.append({"name": name, "address": address})
    return wallets or [wallet.copy() for wallet in _FALLBACK_WALLETS]


def _margin_snapshot(total_attempted: int, total_usdc_moved: float) -> dict[str, Any]:
    eth_l1_gas_per_tx = 0.50
    generic_l2_gas_per_tx = 0.003
    eth_l1_counterfactual_gas = round(total_attempted * eth_l1_gas_per_tx, 2)
    generic_l2_counterfactual_gas = round(total_attempted * generic_l2_gas_per_tx, 3)
    overhead_multiple = round(
        (eth_l1_counterfactual_gas / total_usdc_moved) if total_usdc_moved > 0 else 0.0,
        1,
    )
    return {
        "arc_total_value_usdc": round(total_usdc_moved, 6),
        "eth_l1_counterfactual_gas_usd": eth_l1_counterfactual_gas,
        "generic_l2_counterfactual_gas_usd": generic_l2_counterfactual_gas,
        "eth_l1_overhead_multiple": overhead_multiple,
        "explanation": (
            "The same batch would burn far more in traditional gas than the value moved. "
            "Arc keeps per-action economics viable."
        ),
    }


def _public_transaction(tx: dict[str, Any]) -> dict[str, Any]:
    public = dict(tx)
    tx_hash = str(public.get("tx_hash") or "").strip()
    if tx_hash:
        public["explorer_url"] = f"https://testnet.arcscan.app/tx/{tx_hash}"
    return public


def _evidence_file_label(evidence_path: Path | None) -> str:
    if evidence_path is None:
        return "repo-fallback"
    try:
        return str(evidence_path.relative_to(_ROOT_DIR))
    except ValueError:
        return "external-local-evidence"


@router.get("/summary")
def get_hackathon_summary() -> dict[str, Any]:
    settings = get_settings()
    evidence_path, evidence = _load_latest_evidence()
    summary = cast(
        dict[str, Any],
        (
            evidence.get("summary")
            if isinstance(evidence, dict) and isinstance(evidence.get("summary"), dict)
            else _FALLBACK_SUMMARY
        ),
    )
    transactions = cast(
        list[dict[str, Any]],
        (
            evidence.get("transactions")
            if isinstance(evidence, dict) and isinstance(evidence.get("transactions"), list)
            else _FALLBACK_TRANSACTIONS
        ),
    )
    wallets = _wallet_inventory(evidence)
    connectors = connector_status(settings)

    total_attempted = int(summary.get("total_attempted", len(transactions)))
    successful = int(summary.get("successful", total_attempted))
    failed = int(summary.get("failed", 0))
    total_usdc_moved = float(summary.get("total_usdc_moved", 0.0))
    generated_at = (
        str(evidence.get("generated_at"))
        if isinstance(evidence, dict) and evidence.get("generated_at")
        else None
    )
    wallet_set_id = (
        str(evidence.get("wallet_set_id"))
        if isinstance(evidence, dict) and evidence.get("wallet_set_id")
        else (settings.circle_wallet_set_id or None)
    )
    all_transactions = (
        [_public_transaction(tx) for tx in reversed(transactions)] if transactions else []
    )
    latest_transactions = all_transactions[:8]
    treasury_address = connectors.treasury_address or next(
        (wallet["address"] for wallet in wallets if wallet["name"] == "treasury"),
        None,
    )

    return {
        "ok": True,
        "project": "TTM Agent Market",
        "evidence_kind": "synthetic" if evidence and evidence.get("synthetic") is True else ("local-records" if evidence else "none"),
        "tracks": _TRACKS,
        "evidence_file": _evidence_file_label(evidence_path),
        "proof": {
            "wallet_set_id": wallet_set_id,
            "generated_at": generated_at,
            "transactions_total": total_attempted,
            "transactions_successful": successful,
            "transactions_failed": failed,
            "success_rate_pct": round((successful / total_attempted) * 100.0, 2)
            if total_attempted
            else 0.0,
            "latency_p50_ms": int(summary.get("latency_p50_ms", 0)),
            "latency_p95_ms": int(summary.get("latency_p95_ms", 0)),
            "throughput_tx_per_min": float(summary.get("throughput_tx_per_min", 0.0)),
            "total_usdc_moved": total_usdc_moved,
            "amount_per_tx_usdc": float(summary.get("amount_per_tx_usdc", 0.0)),
            "transactions_listed": len(all_transactions),
            "sample_tx_hash": latest_transactions[0]["tx_hash"] if latest_transactions else None,
            "explorer_base_url": "https://testnet.arcscan.app",
        },
        "margin": _margin_snapshot(total_attempted, total_usdc_moved),
        "arc": ping_arc_network() if settings.enable_demo_job_routes else {"status": "offline", "reason": "Demo network routes are disabled"},
        "circle": {
            "wallet_set_id": wallet_set_id,
            "wallets_configured": len(wallets) if wallets else connectors.wallets_configured,
            "wallets_loaded": connectors.wallets_loaded,
            "treasury_address": treasury_address,
            "wallets": wallets,
        },
        "guardrails": evaluate_guardrails(settings).model_dump(mode="json"),
        "connectors": connectors.model_dump(mode="json"),
        "catalog": [item.model_dump(mode="json") for item in get_catalog()],
        "demo_jobs": [job.model_dump(mode="json") for job in list_demo_jobs()],
        "transactions": all_transactions,
        "latest_transactions": latest_transactions,
        "arc_jobs": [job.model_dump(mode="json") for job in get_arc_jobs(limit=5)],
    }

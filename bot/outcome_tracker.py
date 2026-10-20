from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API_SRC = ROOT / "services" / "api" / "src"
if str(API_SRC) not in sys.path:
    sys.path.insert(0, str(API_SRC))

from sqlalchemy import or_  # noqa: E402

from tothemoon_api.database import SessionLocal  # noqa: E402
from tothemoon_api.db_models import StrategyOutcome, utcnow_naive  # noqa: E402
from tothemoon_api.external.market import get_token_market_data  # noqa: E402

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _decimal(value: object) -> Decimal:
    return Decimal(str(value))


def _price_for(address: str) -> Decimal | None:
    market = get_token_market_data(address)
    price = market.get("price") or market.get("price_usd")
    if price is None:
        return None
    parsed = _decimal(price)
    return parsed if parsed > 0 else None


def _paper_pnl_brl(outcome: StrategyOutcome, price: Decimal) -> Decimal | None:
    if outcome.paper_position is None:
        return None
    entry = outcome.entry_price or outcome.paper_position.entry_price
    if not entry or entry <= 0:
        return None
    amount = _decimal(outcome.paper_position.amount)
    gross = (price - _decimal(entry)) / _decimal(entry) * amount
    fees = _decimal(outcome.paper_position.fees_total or 0)
    return gross - fees


def _classification(outcome: StrategyOutcome, price: Decimal, pnl: Decimal | None) -> str:
    entry = outcome.entry_price or (
        outcome.paper_position.entry_price if outcome.paper_position is not None else None
    )
    if entry is None or entry <= 0:
        return "sideways"
    change = (price - _decimal(entry)) / _decimal(entry)
    if change <= Decimal("-0.70"):
        return "rug"
    if pnl is None:
        return "sideways"
    if pnl > 0:
        return "winner"
    if abs(change) < Decimal("0.05"):
        return "sideways"
    return "loser"


def process_due_outcomes() -> dict[str, int]:
    now = utcnow_naive()
    followup_24h = timedelta(hours=_env_int("TTM_OUTCOME_FOLLOWUP_HOURS_24H", 24))
    followup_7d = timedelta(hours=_env_int("TTM_OUTCOME_FOLLOWUP_HOURS_7D", 168))
    processed = 0
    errors = 0
    with SessionLocal() as db:
        rows = (
            db.query(StrategyOutcome)
            .filter(
                StrategyOutcome.decision == "approved",
                StrategyOutcome.paper_position_id.is_not(None),
                or_(
                    StrategyOutcome.price_24h.is_(None),
                    StrategyOutcome.price_7d.is_(None),
                ),
            )
            .order_by(StrategyOutcome.created_at.asc(), StrategyOutcome.id.asc())
            .limit(250)
            .all()
        )
        for outcome in rows:
            due_24h = outcome.price_24h is None and outcome.created_at <= now - followup_24h
            due_7d = outcome.price_7d is None and outcome.created_at <= now - followup_7d
            if not due_24h and not due_7d:
                continue
            try:
                price = _price_for(outcome.candidate_address)
                if price is None:
                    continue
                if due_24h:
                    outcome.price_24h = price
                if due_7d:
                    outcome.price_7d = price
                pnl = _paper_pnl_brl(outcome, price)
                outcome.paper_pnl_brl = pnl
                outcome.classification = _classification(outcome, price, pnl)
                outcome.updated_at = now
                processed += 1
            except Exception as exc:
                errors += 1
                logger.warning(
                    "strategy_outcome_update_failed",
                    extra={"outcome_id": outcome.id, "error": str(exc)},
                )
        db.commit()
    return {"processed": processed, "errors": errors}


def run_forever(interval_seconds: int) -> None:
    while True:
        result = process_due_outcomes()
        logger.info("strategy_outcome_tracker_cycle", extra=result)
        time.sleep(interval_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Track paper strategy outcomes")
    parser.add_argument("--once", action="store_true", help="Run one update cycle and exit")
    parser.add_argument("--interval", type=int, default=3600, help="Loop interval seconds")
    args = parser.parse_args()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    if args.once:
        print(process_due_outcomes())
    else:
        run_forever(max(args.interval, 60))


if __name__ == "__main__":
    main()

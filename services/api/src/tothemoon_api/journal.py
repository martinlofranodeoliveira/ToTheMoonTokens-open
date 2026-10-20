from __future__ import annotations

import json
import os
import threading
import uuid
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from .models import AggregateBucket, PaperTradeRecord, PerformanceAggregates


def _journal_path() -> Path:
    configured = os.getenv("PAPER_JOURNAL_FILE", ".nexus/paper_journal.json")
    return Path(configured)


_CACHE_LOCK = threading.Lock()
_CACHE: tuple[str, float, int, list[PaperTradeRecord]] | None = None


def _invalidate_cache() -> None:
    global _CACHE
    with _CACHE_LOCK:
        _CACHE = None


def _load_raw() -> list[dict[str, object]]:
    path = _journal_path()
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def load_trades() -> list[PaperTradeRecord]:
    global _CACHE
    path = _journal_path()
    if not path.exists():
        with _CACHE_LOCK:
            _CACHE = None
        return []

    try:
        stat = path.stat()
    except OSError:
        return []

    key = (str(path), stat.st_mtime, stat.st_size)
    with _CACHE_LOCK:
        cached = _CACHE
        if cached is not None and cached[:3] == key:
            return list(cached[3])

    trades: list[PaperTradeRecord] = []
    for payload in _load_raw():
        try:
            trades.append(PaperTradeRecord.model_validate(payload))
        except Exception:
            continue

    with _CACHE_LOCK:
        _CACHE = (str(path), stat.st_mtime, stat.st_size, list(trades))

    return trades


def _persist(records: list[PaperTradeRecord]) -> None:
    path = _journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.parent / f".{path.name}.tmp"
    tmp_path.write_text(
        json.dumps([record.model_dump(mode="json") for record in records], indent=2),
        encoding="utf-8",
    )
    tmp_path.replace(path)
    _invalidate_cache()


def add_trade(record: PaperTradeRecord) -> PaperTradeRecord:
    if not record.id:
        record.id = uuid.uuid4().hex
    records = load_trades()
    records.append(record)
    _persist(records)
    return record


def record_trade(trade: PaperTradeRecord) -> PaperTradeRecord:
    return add_trade(trade)


def get_journal(limit: int | None = None) -> list[PaperTradeRecord]:
    trades = sorted(load_trades(), key=lambda trade: trade.entry_time, reverse=True)
    if limit is None:
        return trades
    return trades[: max(limit, 0)]


def get_recent_trades(limit: int = 12) -> list[PaperTradeRecord]:
    return get_journal(limit=limit)


def clear_trades() -> None:
    path = _journal_path()
    if path.exists():
        path.unlink()
    _invalidate_cache()


def clear_journal() -> None:
    clear_trades()


def _bucket(records: Iterable[PaperTradeRecord]) -> AggregateBucket:
    items = list(records)
    total_trades = len(items)
    realized = [item.pnl for item in items if item.pnl is not None]
    total_pnl = round(sum(realized), 4)
    closed = [item for item in items if item.status == "closed" or item.pnl is not None]
    wins = sum(
        1
        for item in closed
        if item.outcome == "win" or (item.outcome == "open" and (item.pnl or 0) > 0)
    )
    drawdowns = [item.drawdown for item in items if item.drawdown is not None]
    win_rate_pct = round((wins / len(closed)) * 100, 4) if closed else 0.0
    average_drawdown = round(sum(drawdowns) / len(drawdowns), 4) if drawdowns else 0.0
    return AggregateBucket(
        total_trades=total_trades,
        total_pnl=total_pnl,
        win_rate_pct=win_rate_pct,
        average_drawdown=average_drawdown,
    )


def get_performance_aggregate() -> PerformanceAggregates:
    return get_performance_aggregates()


def get_performance_aggregates(
    strategy_id: str | None = None,
    symbol: str | None = None,
    timeframe: str | None = None,
) -> PerformanceAggregates:
    trades = load_trades()
    if strategy_id:
        trades = [trade for trade in trades if trade.strategy_id == strategy_id]
    if symbol:
        trades = [trade for trade in trades if trade.symbol == symbol]
    if timeframe:
        trades = [trade for trade in trades if trade.timeframe == timeframe]

    by_strategy: dict[str, list[PaperTradeRecord]] = defaultdict(list)
    by_symbol: dict[str, list[PaperTradeRecord]] = defaultdict(list)
    by_timeframe: dict[str, list[PaperTradeRecord]] = defaultdict(list)
    outcomes_by_regime: dict[str, dict[str, int]] = {}

    for trade in trades:
        by_strategy[trade.strategy_id].append(trade)
        by_symbol[trade.symbol].append(trade)
        by_timeframe[trade.timeframe].append(trade)
        regime = trade.market_regime
        outcomes_by_regime.setdefault(regime, {"win": 0, "loss": 0, "breakeven": 0, "open": 0})
        outcomes_by_regime[regime][trade.outcome] += 1

    total_pnl = round(sum(trade.pnl or 0.0 for trade in trades), 4)
    closed_trades = [
        trade for trade in trades if trade.outcome != "open" or trade.status == "closed"
    ]
    wins = len([trade for trade in closed_trades if trade.outcome == "win"])
    gross_wins = sum(
        trade.pnl for trade in closed_trades if trade.pnl is not None and trade.pnl > 0
    )
    gross_losses = sum(
        abs(trade.pnl) for trade in closed_trades if trade.pnl is not None and trade.pnl < 0
    )
    profit_factor = (
        (gross_wins / gross_losses) if gross_losses > 0 else (gross_wins if gross_wins > 0 else 0.0)
    )
    summary_bucket = _bucket(trades)

    return PerformanceAggregates(
        total_trades=len(trades),
        total_pnl=total_pnl,
        win_rate=round((wins / len(closed_trades)), 4) if closed_trades else 0.0,
        win_rate_pct=summary_bucket.win_rate_pct,
        profit_factor=round(profit_factor, 4),
        average_drawdown=summary_bucket.average_drawdown,
        outcomes_by_regime=outcomes_by_regime,
        by_strategy={key: _bucket(value) for key, value in sorted(by_strategy.items())},
        by_symbol={key: _bucket(value) for key, value in sorted(by_symbol.items())},
        by_timeframe={key: _bucket(value) for key, value in sorted(by_timeframe.items())},
    )

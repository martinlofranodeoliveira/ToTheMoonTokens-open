from __future__ import annotations

import hashlib
import json
import math
import random
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from websockets.sync.client import connect

from .config import get_settings
from .models import Candle, MarketRegime
from .observability import get_logger

settings = get_settings()
log = get_logger(__name__)


class ExchangeDegradationError(RuntimeError):
    pass


class MarketDataError(ExchangeDegradationError):
    pass


class ConnectorState:
    def __init__(self) -> None:
        self.is_healthy: bool = True
        self.last_latency_ms: float = 0.0
        self.last_error: str | None = None
        self.reconnect_count: int = 0
        self.last_heartbeat: datetime | None = None

    def degrade(self, error: str) -> None:
        self.is_healthy = False
        self.last_error = error

    def recover(self, latency_ms: float | None = None) -> None:
        self.is_healthy = True
        self.last_error = None
        self.last_heartbeat = datetime.now(UTC)
        if latency_ms is not None:
            self.last_latency_ms = latency_ms


connector_state = ConnectorState()


def _seed_for(dataset_id: str, seed: int) -> int:
    material = f"{dataset_id}:{seed}".encode()
    return int(hashlib.sha256(material).hexdigest()[:16], 16)


def _regime_for_index(index: int, length: int, rng: random.Random) -> MarketRegime:
    third = max(length // 3, 1)
    segment = min(index // third, 2)
    if segment == 0:
        return "bull"
    if segment == 1:
        return "chop"
    if rng.random() > 0.45:
        return "bear"
    return "chop"


def _classify_live_regime(closes: list[float]) -> MarketRegime:
    if len(closes) < 12:
        return "chop"
    baseline = closes[0]
    if baseline <= 0:
        return "chop"
    latest = closes[-1]
    rolling_mean = sum(closes[-6:]) / min(len(closes), 6)
    return_pct = ((latest - baseline) / baseline) * 100
    deviation_pct = ((latest - rolling_mean) / rolling_mean) * 100 if rolling_mean else 0.0
    range_pct = ((max(closes) - min(closes)) / baseline) * 100

    if return_pct >= 0.4 and deviation_pct >= 0.12:
        return "bull"
    if return_pct <= -0.4 and deviation_pct <= -0.12:
        return "bear"
    if range_pct <= 0.45 or abs(return_pct) <= 0.2:
        return "chop"
    if abs(return_pct) >= 0.8:
        return "bull" if return_pct > 0 else "bear"
    return "chop"


def _annotate_live_regimes(candles: list[Candle]) -> list[Candle]:
    annotated: list[Candle] = []
    closes: list[float] = []
    for candle in candles:
        closes.append(candle.close)
        regime = _classify_live_regime(closes[-20:])
        annotated.append(candle.model_copy(update={"regime": regime}))
    return annotated


def generate_sample_candles(
    length: int = 180,
    dataset_id: str = "synthetic",
    seed: int = 42,
) -> list[Candle]:
    candles: list[Candle] = []
    rng = random.Random(_seed_for(dataset_id, seed))
    start = datetime(2026, 1, 1, tzinfo=UTC)
    price = 100.0 + rng.uniform(-3.0, 3.0)

    for index in range(length):
        regime = _regime_for_index(index, length, rng)
        drift = {"bull": 0.28, "bear": -0.24, "chop": 0.03}[regime]
        cycle = math.sin(index / 8) * 1.8
        pullback = math.cos(index / 17) * 0.9
        noise = rng.uniform(-1.5, 1.5)
        close = max(1.0, price + drift + cycle + pullback + noise)
        open_price = price
        high = max(open_price, close) + abs(rng.uniform(0.4, 1.6))
        low = min(open_price, close) - abs(rng.uniform(0.4, 1.4))
        volume = 900 + rng.randint(0, 180) + (index % 24) * 15
        candles.append(
            Candle(
                timestamp=start + timedelta(minutes=index),
                open=round(open_price, 4),
                high=round(high, 4),
                low=round(low, 4),
                close=round(close, 4),
                volume=float(volume),
                regime=regime,
            )
        )
        price = close

    return candles


_SHARED_CLIENT_TIMEOUT = 10.0
_SHARED_CLIENT_LIMITS = httpx.Limits(max_keepalive_connections=10, max_connections=20)
_SHARED_CLIENT: httpx.Client | None = None
_SHARED_CLIENT_LOCK = threading.Lock()


def _shared_client() -> httpx.Client:
    global _SHARED_CLIENT
    if _SHARED_CLIENT is not None:
        return _SHARED_CLIENT
    with _SHARED_CLIENT_LOCK:
        if _SHARED_CLIENT is None:
            _SHARED_CLIENT = httpx.Client(
                timeout=_SHARED_CLIENT_TIMEOUT,
                limits=_SHARED_CLIENT_LIMITS,
            )
    return _SHARED_CLIENT


def _fetch_json(url: str, params: dict[str, Any]) -> Any:
    started = time.perf_counter()
    response = _shared_client().get(url, params=params)
    response.raise_for_status()
    connector_state.recover((time.perf_counter() - started) * 1000)
    return response.json()


class BinanceMarketData:
    def __init__(
        self,
        *,
        base_url: str = settings.binance_testnet_base_url,
        ws_url: str = settings.binance_user_data_stream_url,
        timeout_seconds: float = 5.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.ws_url = ws_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout_seconds)
        self.operational_status: dict[str, object] = {
            "status": "unknown",
            "latency_ms": 0.0,
            "last_error": None,
            "last_stream": None,
            "sample_count": 0,
        }

    def _mark_degraded(self, error: Exception, *, stream_name: str | None = None) -> None:
        message = str(error)
        connector_state.degrade(message)
        connector_state.reconnect_count += 1
        self.operational_status.update(
            {
                "status": "degraded",
                "latency_ms": 0.0,
                "last_error": message,
                "last_stream": stream_name,
            }
        )

    def _mark_recovered(self, *, latency_ms: float | None = None, sample_count: int = 0) -> None:
        connector_state.recover(latency_ms)
        self.operational_status.update(
            {
                "status": "online",
                "latency_ms": round(latency_ms or connector_state.last_latency_ms, 2),
                "last_error": None,
                "sample_count": sample_count,
            }
        )

    def ping(self) -> dict[str, object]:
        started = time.perf_counter()
        try:
            response = self.client.get("/api/v3/ping")
            response.raise_for_status()
            self._mark_recovered(latency_ms=(time.perf_counter() - started) * 1000)
        except Exception as exc:
            self._mark_degraded(exc)
            log.warning("market_ping_failed", error=str(exc), base_url=self.base_url)
        return dict(self.operational_status)

    def fetch_klines(self, symbol: str, interval: str, limit: int = 180) -> list[Candle]:
        started = time.perf_counter()
        try:
            response = self.client.get(
                "/api/v3/klines",
                params={"symbol": symbol, "interval": interval, "limit": limit},
            )
            response.raise_for_status()
            rows = response.json()
            candles: list[Candle] = []
            for index, row in enumerate(rows):
                regime = _regime_for_index(index, max(len(rows), 1), random.Random(index + 7))
                candles.append(
                    Candle(
                        timestamp=datetime.fromtimestamp(row[0] / 1000.0, tz=UTC),
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5]),
                        regime=regime,
                    )
                )
            if not candles:
                raise ExchangeDegradationError("No candles returned by exchange.")
            candles = _annotate_live_regimes(candles)
            self._mark_recovered(
                latency_ms=(time.perf_counter() - started) * 1000,
                sample_count=len(candles),
            )
            return candles
        except Exception as exc:
            self._mark_degraded(exc)
            log.warning(
                "market_klines_fallback",
                symbol=symbol,
                interval=interval,
                limit=limit,
                error=str(exc),
            )
            return generate_sample_candles(
                length=limit, dataset_id=f"{symbol}:{interval}", seed=limit
            )

    def fetch_ticker(self, symbol: str) -> dict[str, object]:
        started = time.perf_counter()
        try:
            response = self.client.get("/api/v3/ticker/24hr", params={"symbol": symbol})
            response.raise_for_status()
            payload = response.json()
            self._mark_recovered(latency_ms=(time.perf_counter() - started) * 1000)
            return payload
        except Exception as exc:
            self._mark_degraded(exc)
            raise MarketDataError(f"failed to fetch ticker for {symbol}: {exc}") from exc

    def fetch_depth(self, symbol: str, limit: int = 100) -> dict[str, object]:
        started = time.perf_counter()
        try:
            response = self.client.get("/api/v3/depth", params={"symbol": symbol, "limit": limit})
            response.raise_for_status()
            payload = response.json()
            self._mark_recovered(latency_ms=(time.perf_counter() - started) * 1000)
            return payload
        except Exception as exc:
            self._mark_degraded(exc)
            raise MarketDataError(f"failed to fetch depth for {symbol}: {exc}") from exc

    def listen_stream(self, stream_name: str, max_messages: int = 1) -> list[dict[str, object]]:
        messages: list[dict[str, object]] = []
        try:
            with connect(f"{self.ws_url}/{stream_name}") as websocket:
                for _ in range(max_messages):
                    raw_message = websocket.recv(timeout=5.0)
                    messages.append(json.loads(raw_message))
            self._mark_recovered(sample_count=len(messages))
            self.operational_status["last_stream"] = stream_name
            return messages
        except Exception as exc:
            self._mark_degraded(exc, stream_name=stream_name)
            log.warning("market_stream_degraded", stream_name=stream_name, error=str(exc))
            return messages


def get_historical_candles(symbol: str, interval: str, limit: int) -> list[Candle]:
    url = f"{settings.binance_testnet_base_url}/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}

    try:
        rows = _fetch_json(url, params)
        candles: list[Candle] = []
        for index, row in enumerate(rows):
            regime = _regime_for_index(index, max(len(rows), 1), random.Random(index + 7))
            candles.append(
                Candle(
                    timestamp=datetime.fromtimestamp(row[0] / 1000, tz=UTC),
                    open=float(row[1]),
                    high=float(row[2]),
                    low=float(row[3]),
                    close=float(row[4]),
                    volume=float(row[5]),
                    regime=regime,
                )
            )
        if not candles:
            raise ExchangeDegradationError("No candles returned by exchange.")
        return _annotate_live_regimes(candles)
    except Exception as exc:
        connector_state.degrade(str(exc))
        connector_state.reconnect_count += 1
        return generate_sample_candles(length=limit, dataset_id=f"{symbol}:{interval}", seed=limit)


def get_ticker(symbol: str) -> dict[str, Any]:
    try:
        return _fetch_json(
            f"{settings.binance_testnet_base_url}/api/v3/ticker/24hr", {"symbol": symbol}
        )
    except Exception as exc:
        connector_state.degrade(str(exc))
        connector_state.reconnect_count += 1
        raise ExchangeDegradationError(str(exc)) from exc


def get_depth(symbol: str, limit: int = 100) -> dict[str, Any]:
    try:
        return _fetch_json(
            f"{settings.binance_testnet_base_url}/api/v3/depth",
            {"symbol": symbol, "limit": limit},
        )
    except Exception as exc:
        connector_state.degrade(str(exc))
        connector_state.reconnect_count += 1
        raise ExchangeDegradationError(str(exc)) from exc

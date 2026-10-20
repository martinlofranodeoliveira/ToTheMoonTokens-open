from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx

import tothemoon_api.market_data as market_data
from tothemoon_api.market_data import BinanceMarketData, MarketDataError


def setup_function() -> None:
    market_data.connector_state.is_healthy = True
    market_data.connector_state.last_error = None
    market_data.connector_state.last_latency_ms = 0.0
    market_data.connector_state.reconnect_count = 0
    market_data.connector_state.last_heartbeat = None


def test_ping_success_marks_connector_online():
    market = BinanceMarketData()
    with patch.object(market.client, "get") as mock_get:
        response = MagicMock()
        response.raise_for_status.return_value = None
        mock_get.return_value = response

        status = market.ping()

    assert status["status"] == "online"
    assert status["latency_ms"] >= 0
    assert status["last_error"] is None


def test_fetch_klines_falls_back_to_sample_candles_on_api_failure():
    market = BinanceMarketData()
    with patch.object(market.client, "get") as mock_get:
        mock_get.side_effect = httpx.RequestError("network down")

        candles = market.fetch_klines("BTCUSDT", "1m", limit=8)

    assert len(candles) == 8
    assert market.operational_status["status"] == "degraded"
    assert "network down" in str(market.operational_status["last_error"])


def test_fetch_klines_infers_bear_regime_from_live_price_action():
    market = BinanceMarketData()
    rows = []
    base_ts = 1_700_000_000_000
    price = 100.0
    for index in range(24):
        price -= 0.35
        rows.append(
            [
                base_ts + (index * 60_000),
                f"{price + 0.15:.4f}",
                f"{price + 0.2:.4f}",
                f"{price - 0.2:.4f}",
                f"{price:.4f}",
                "1200",
            ]
        )

    with patch.object(market.client, "get") as mock_get:
        response = MagicMock()
        response.raise_for_status.return_value = None
        response.json.return_value = rows
        mock_get.return_value = response
        candles = market.fetch_klines("BTCUSDT", "1m", limit=24)

    assert candles[-1].regime == "bear"
    assert market.operational_status["status"] == "online"


def test_fetch_ticker_raises_market_data_error_on_failure():
    market = BinanceMarketData()
    with patch.object(market.client, "get") as mock_get:
        mock_get.side_effect = httpx.HTTPStatusError(
            "bad gateway",
            request=MagicMock(),
            response=MagicMock(),
        )

        try:
            market.fetch_ticker("BTCUSDT")
        except MarketDataError as exc:
            assert "failed to fetch ticker" in str(exc)
        else:
            raise AssertionError("expected MarketDataError")


def test_fetch_depth_raises_market_data_error_on_failure():
    market = BinanceMarketData()
    with patch.object(market.client, "get") as mock_get:
        mock_get.side_effect = httpx.RequestError("depth unavailable")

        try:
            market.fetch_depth("BTCUSDT")
        except MarketDataError as exc:
            assert "failed to fetch depth" in str(exc)
        else:
            raise AssertionError("expected MarketDataError")


def test_listen_stream_degrades_gracefully_on_websocket_failure():
    market = BinanceMarketData()
    with patch("tothemoon_api.market_data.connect") as mock_connect:
        mock_connect.side_effect = RuntimeError("ws refused")
        messages = market.listen_stream("btcusdt@trade")

    assert messages == []
    assert market.operational_status["status"] == "degraded"
    assert market.operational_status["last_stream"] == "btcusdt@trade"


def test_historical_candles_fall_back_to_synthetic_when_exchange_fails(monkeypatch):
    def fake_fetch_json(url: str, params: dict[str, object]) -> object:
        raise RuntimeError("exchange temporarily unavailable")

    monkeypatch.setattr(market_data, "_fetch_json", fake_fetch_json)

    candles = market_data.get_historical_candles("BTCUSDT", "1m", 12)

    assert len(candles) == 12
    assert market_data.connector_state.is_healthy is False
    assert market_data.connector_state.last_error == "exchange temporarily unavailable"
    assert market_data.connector_state.reconnect_count == 1

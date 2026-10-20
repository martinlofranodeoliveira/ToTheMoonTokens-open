from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[1]
API_SRC = ROOT / "services" / "api" / "src"
if str(API_SRC) not in sys.path:
    sys.path.insert(0, str(API_SRC))

try:
    from .discord_notifier import DiscordCommand, DiscordNotifier
except ImportError:  # pragma: no cover - supports python bot/main_supervisor.py
    from discord_notifier import DiscordCommand, DiscordNotifier

logger = logging.getLogger(__name__)

PAUSE_FILE = Path(os.getenv("TTM_SUPERVISOR_PAUSE_FILE", "/tmp/ttm-supervisor.paused"))
DISCORD_CURSOR_FILE = Path(
    os.getenv("TTM_DISCORD_CURSOR_FILE", "/tmp/ttm-discord-last-message-id")
)
KILLED_PROFILES_FILE = Path(
    os.getenv("TTM_KILLED_PROFILES_FILE", "/tmp/ttm-killed-strategy-profiles.json")
)
SIGNAL_STATE_FILE = Path(os.getenv("TTM_SIGNAL_STATE_FILE", "/tmp/ttm-signal-state.json"))

STRATEGY_FAMILY_CONTRACT = {
    "A": "liquid_momentum",
    "B": "early_discovery",
    "C": "whale_accumulation",
    "D": "oversold_quality",
    "E": "volume_spike",
    "F": "narrative_themed",
}
ACTIVE_STRATEGY_FAMILIES = {"A", "B", "D", "F"}
BROWSER_LIQUID_PROFILE_TARGET = 5
ONCHAIN_SCOUT_PROFILE_TARGET = 3
BROWSER_FALLBACK_USER_AGENT = "ToTheMoonTokens-Hermes/1.0"
COINGECKO_TRENDING_URL = "https://api.coingecko.com/api/v3/search/trending"
BINANCE_TICKER_24H_URL = "https://api.binance.com/api/v3/ticker/24hr"
BINANCE_TICKER_24H_FALLBACK_URL = "https://data-api.binance.vision/api/v3/ticker/24hr"
BINANCE_TICKER_PRICE_URL = "https://api.binance.com/api/v3/ticker/price"
BINANCE_TICKER_PRICE_FALLBACK_URL = "https://data-api.binance.vision/api/v3/ticker/price"
QUOTE_SUFFIXES = ("FDUSD", "USDT", "USDC", "BUSD", "TUSD")
STABLE_SYMBOLS = {"BUSD", "DAI", "FDUSD", "TUSD", "USDC", "USDE", "USDT"}


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def _json(data: object) -> str:
    return json.dumps(data, ensure_ascii=True, sort_keys=True, default=str)


def _load_json_object(path: Path) -> dict[str, object]:
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_json_object(path: Path, data: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _market_symbol(value: object) -> str | None:
    symbol = "".join(character for character in str(value or "").upper() if character.isalnum())
    if not 2 <= len(symbol) <= 12 or symbol in STABLE_SYMBOLS:
        return None
    return symbol


def _base_symbol_from_pair(value: object) -> str | None:
    pair = "".join(character for character in str(value or "").upper() if character.isalnum())
    for suffix in QUOTE_SUFFIXES:
        if pair.endswith(suffix) and len(pair) > len(suffix) + 1:
            base = pair[: -len(suffix)]
            if base.endswith(("BULL", "BEAR", "DOWN")):
                return None
            return _market_symbol(base)
    return None


def _normalize_binance_pair(value: object) -> str | None:
    pair = "".join(character for character in str(value or "").upper() if character.isalnum())
    if not 3 <= len(pair) <= 20:
        return None
    if any(pair.endswith(suffix) and len(pair) > len(suffix) + 1 for suffix in QUOTE_SUFFIXES):
        return pair
    symbol = _market_symbol(pair)
    return f"{symbol}USDT" if symbol else None


def _parse_decimal_text(value: str) -> Decimal:
    return Decimal(value.strip().replace(",", "."))


def _utc_now_text() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _manual_position_id(symbol: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    return f"{symbol}-{stamp}"


def _strategy_slot_category(family: str) -> str:
    return "onchain_scout" if family.strip().upper() == "B" else "liquid_binance"


def _coingecko_trending_context(data: object) -> list[dict[str, object]]:
    if not isinstance(data, dict):
        return []
    coins = data.get("coins")
    if not isinstance(coins, list):
        return []
    rows: list[dict[str, object]] = []
    seen: set[str] = set()
    for row in coins:
        item = row.get("item") if isinstance(row, dict) else None
        if not isinstance(item, dict):
            continue
        symbol = _market_symbol(item.get("symbol"))
        if symbol is None or symbol in seen:
            continue
        seen.add(symbol)
        rows.append(
            {
                "symbol": symbol,
                "name": item.get("name"),
                "market_cap_rank": item.get("market_cap_rank"),
                "score": item.get("score"),
                "source": "coingecko_trending",
            }
        )
        if len(rows) >= 12:
            break
    return rows


def _binance_market_context(data: object) -> dict[str, list[dict[str, object]]]:
    if not isinstance(data, list):
        return {"liquid": [], "top_movers": [], "oversold": []}
    by_symbol: dict[str, dict[str, object]] = {}
    for row in data:
        if not isinstance(row, dict):
            continue
        symbol = _base_symbol_from_pair(row.get("symbol"))
        if symbol is None:
            continue
        quote_volume = _safe_float(row.get("quoteVolume"))
        price_change_pct = _safe_float(row.get("priceChangePercent"))
        if quote_volume < 1_000_000:
            continue
        item = {
            "symbol": symbol,
            "pair": row.get("symbol"),
            "last_price": round(_safe_float(row.get("lastPrice")), 10),
            "price_change_pct": round(price_change_pct, 6),
            "quote_volume": round(quote_volume, 2),
            "source": "binance_24h_ticker",
        }
        previous = by_symbol.get(symbol)
        if previous is None or quote_volume > _safe_float(previous.get("quote_volume")):
            by_symbol[symbol] = item
    rows = list(by_symbol.values())
    liquid = sorted(rows, key=lambda item: _safe_float(item.get("quote_volume")), reverse=True)
    top_movers = sorted(
        (item for item in rows if _safe_float(item.get("price_change_pct")) > 0),
        key=lambda item: (
            _safe_float(item.get("price_change_pct")),
            _safe_float(item.get("quote_volume")),
        ),
        reverse=True,
    )
    oversold = sorted(
        (item for item in rows if _safe_float(item.get("price_change_pct")) < 0),
        key=lambda item: (
            _safe_float(item.get("price_change_pct")),
            -_safe_float(item.get("quote_volume")),
        ),
    )
    return {
        "liquid": liquid[:12],
        "top_movers": top_movers[:12],
        "oversold": oversold[:8],
    }


@dataclass(frozen=True)
class StrategyProfile:
    name: str
    query: str
    limit: int
    min_volume: float
    min_momentum: float
    min_liquidity: float
    rationale: str = ""
    family: str = "F"
    min_market_cap: float = 0.0


def _infer_family(name: str, explicit: object = None) -> str | None:
    family = str(explicit or "").strip().upper()
    if family in STRATEGY_FAMILY_CONTRACT:
        return family
    lowered = name.lower()
    if "liquid_momentum" in lowered:
        return "A"
    if "fresh" in lowered or "early_discovery" in lowered:
        return "B"
    if "whale" in lowered or "accumulation" in lowered or "silent" in lowered:
        return "C"
    if "oversold" in lowered or "mean_reversion" in lowered:
        return "D"
    if "volume_spike" in lowered:
        return "E"
    if "narrative" in lowered or "themed" in lowered:
        return "F"
    return None


def _fallback_strategy_profiles(
    *,
    query: str,
    limit: int,
    min_volume: float,
    min_momentum: float,
    min_liquidity: float,
) -> list[StrategyProfile]:
    return [
        StrategyProfile(
            "liquid_momentum_baseline",
            "BTC",
            25,
            max(min_volume, 50_000),
            max(min_momentum, 1.0),
            max(min_liquidity, 25_000),
            "Fallback A seed: any liquid crypto with modest positive momentum.",
            "A",
        ),
        StrategyProfile(
            "liquid_altcoin_rotation",
            "ETH",
            25,
            50_000,
            0.5,
            25_000,
            "Fallback A seed: established liquid altcoin rotation, not only new tokens.",
            "A",
        ),
        StrategyProfile(
            "liquid_layer1_rotation",
            "SUI",
            25,
            30_000,
            0.5,
            20_000,
            "Fallback A seed: liquid layer-1 and large-cap rotation beyond only new tokens.",
            "A",
        ),
        StrategyProfile(
            "liquid_sol_rotation",
            "SOL",
            25,
            50_000,
            0.5,
            25_000,
            "Fallback A seed: liquid SOL rotation for Binance-style paper routing.",
            "A",
        ),
        StrategyProfile(
            "liquid_bnb_rotation",
            "BNB",
            25,
            50_000,
            0.5,
            25_000,
            "Fallback A seed: liquid BNB rotation for Binance-style paper routing.",
            "A",
        ),
        StrategyProfile(
            "fresh_discovery_baseline",
            "pump",
            25,
            10_000,
            5.0,
            5_000,
            "Fallback B seed: new meme tokens with initial traction.",
            "B",
        ),
        StrategyProfile(
            "fresh_meme_scout",
            "meme",
            25,
            8_000,
            4.0,
            4_000,
            "Fallback B seed: new on-chain meme scout with strict paper-only sizing.",
            "B",
        ),
        StrategyProfile(
            "fresh_base_scout",
            "base",
            25,
            8_000,
            3.0,
            4_000,
            "Fallback B seed: new Base/EVM on-chain scout for MetaMask-style routing later.",
            "B",
        ),
        StrategyProfile(
            "oversold_quality_baseline",
            "BTC",
            25,
            50_000,
            -10.0,
            25_000,
            "Fallback D seed: liquid oversold blue-chip and large-cap tokens.",
            "D",
            1_000_000,
        ),
        StrategyProfile(
            "oversold_altcoin_quality",
            "PEPE",
            25,
            30_000,
            -8.0,
            20_000,
            "Fallback D seed: liquid meme/altcoin mean reversion with market-cap floor.",
            "D",
            1_000_000,
        ),
        StrategyProfile(
            "oversold_bnb_quality",
            "BNB",
            25,
            50_000,
            -6.0,
            25_000,
            "Fallback D seed: liquid BNB mean reversion for Binance-style paper routing.",
            "D",
            1_000_000,
        ),
        StrategyProfile(
            "narrative_ai_baseline",
            query
            if len(query.strip()) >= 2 and query.strip().upper() not in {"USDC", "USDT"}
            else "AI",
            25,
            max(20_000, min_volume / 4),
            0.0,
            max(10_000, min_liquidity / 4),
            "Fallback F seed: narrative themed query while Gemini is unavailable.",
            "F",
        ),
        StrategyProfile(
            "large_memecoin_rotation",
            "DOGE",
            25,
            30_000,
            0.0,
            15_000,
            "Fallback F seed: large liquid memecoin rotation, not only fresh launches.",
            "F",
        ),
    ]


def _strategy_profiles_from_env(defaults: list[StrategyProfile]) -> list[StrategyProfile]:
    raw = os.getenv("TTM_STRATEGY_PROFILES", "").strip()
    if not raw:
        return defaults
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("invalid_strategy_profiles_json")
        return defaults
    if not isinstance(data, list):
        logger.warning("invalid_strategy_profiles_shape")
        return defaults
    profiles: list[StrategyProfile] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        try:
            family = _infer_family(str(item["name"]), item.get("family")) or "F"
            if family not in ACTIVE_STRATEGY_FAMILIES:
                continue
            profiles.append(
                StrategyProfile(
                    name=str(item["name"]).strip(),
                    query=str(item["query"]).strip(),
                    limit=int(item.get("limit", 10)),
                    min_volume=float(item.get("min_volume", 10_000)),
                    min_momentum=float(item.get("min_momentum", 1.0)),
                    min_liquidity=float(item.get("min_liquidity", 5_000)),
                    rationale=str(item.get("rationale", "")).strip(),
                    family=family,
                    min_market_cap=float(
                        item.get("min_market_cap", 1_000_000 if family == "D" else 0)
                    ),
                )
            )
        except (KeyError, TypeError, ValueError):
            logger.warning("invalid_strategy_profile", extra={"profile": str(item)[:120]})
    return profiles or defaults


def _load_discord_cursor() -> str | None:
    if not DISCORD_CURSOR_FILE.exists():
        return None
    value = DISCORD_CURSOR_FILE.read_text(encoding="utf-8").strip()
    return value or None


def _save_discord_cursor(message_id: str) -> None:
    if message_id:
        DISCORD_CURSOR_FILE.write_text(message_id, encoding="utf-8")


def _load_killed_profiles() -> set[str]:
    if not KILLED_PROFILES_FILE.exists():
        return set()
    try:
        data = json.loads(KILLED_PROFILES_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(data, list):
        return set()
    return {str(item).strip() for item in data if str(item).strip()}


def _save_killed_profiles(profiles: set[str]) -> None:
    KILLED_PROFILES_FILE.write_text(
        json.dumps(sorted(profiles), ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def _api_error_summary(exc: Exception) -> str:
    if not isinstance(exc, httpx.HTTPStatusError):
        return str(exc)
    request_path = exc.request.url.path
    detail: object = None
    try:
        body = exc.response.json()
        if isinstance(body, dict):
            detail = body.get("detail")
    except ValueError:
        detail = exc.response.text.strip()
    suffix = f": {detail}" if detail else ""
    return f"HTTP {exc.response.status_code} em {request_path}{suffix}"


def _extract_json_array(text: str) -> list[object]:
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        raise ValueError("Gemini did not return a JSON array")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, list):
        raise ValueError("Gemini strategy response must be a JSON array")
    return data


def _slug(value: str, fallback: str) -> str:
    cleaned = "".join(character.lower() if character.isalnum() else "_" for character in value)
    cleaned = "_".join(part for part in cleaned.split("_") if part)
    return (cleaned or fallback)[:40]


def _normalize_family_query(family: str, query: str, index: int) -> str:
    allowed = {
        "A": (
            "BTC",
            "ETH",
            "SOL",
            "WETH",
            "BNB",
            "PEPE",
            "DOGE",
            "XRP",
            "LINK",
            "AVAX",
            "SUI",
            "TON",
        ),
        "B": ("pump", "meme", "base", "SOL"),
        "D": (
            "BTC",
            "ETH",
            "SOL",
            "WETH",
            "BNB",
            "PEPE",
            "DOGE",
            "XRP",
            "LINK",
            "AVAX",
            "SUI",
            "TON",
        ),
        "F": ("AI", "RWA", "DEPIN", "GAME", "PEPE", "DOGE", "SUI", "TON"),
    }
    clean = query.strip()
    family_allowed = allowed.get(family, ("AI",))
    if clean.upper() in {item.upper() for item in family_allowed}:
        return clean
    if len(clean) > 20 or any(not character.isalnum() for character in clean):
        return family_allowed[index % len(family_allowed)]
    return clean


def _zero_candidate_mode(history: dict[str, object]) -> bool:
    runs = history.get("runs")
    if not isinstance(runs, list) or not runs:
        return True
    recent = [row for row in runs[:15] if isinstance(row, dict)]
    return bool(recent) and sum(int(row.get("candidate_count") or 0) for row in recent) == 0


def _coerce_profile(
    item: object,
    index: int,
    *,
    dead_profiles: set[str] | None = None,
    scout_mode: bool = False,
) -> StrategyProfile | None:
    if not isinstance(item, dict):
        return None
    try:
        query = str(item.get("query") or "").strip()[:80]
        if len(query) < 2:
            return None
        name = _slug(str(item.get("name") or ""), f"ai_profile_{index + 1}")
        if name in (dead_profiles or set()) or name in _load_killed_profiles():
            return None
        family = _infer_family(name, item.get("family"))
        if family is None or family not in ACTIVE_STRATEGY_FAMILIES:
            return None
        query = _normalize_family_query(family, query, index)
        limit = max(1, min(25, int(item.get("limit", 10))))
        raw_momentum = float(item.get("min_momentum", 1.0))
        if raw_momentum > 100:
            return None
        min_volume = max(0.0, min(10_000_000.0, float(item.get("min_volume", 10_000))))
        min_momentum = max(-100.0, min(100.0, raw_momentum))
        min_liquidity = max(0.0, min(10_000_000.0, float(item.get("min_liquidity", 5_000))))
        if min_volume < 1_000 and min_liquidity < 1_000:
            return None
        min_market_cap = max(0.0, min(1_000_000_000.0, float(item.get("min_market_cap", 0))))
        if family == "D":
            min_market_cap = max(min_market_cap, 1_000_000.0)
        if scout_mode:
            scout_caps = {
                "A": (100_000.0, 2.0, 50_000.0, 0.0),
                "B": (5_000.0, 3.0, 3_000.0, 0.0),
                "D": (75_000.0, -10.0, 80_000.0, 1_000_000.0),
                "F": (20_000.0, 0.0, 20_000.0, 0.0),
            }
            volume_cap, momentum_cap, liquidity_cap, market_cap = scout_caps[family]
            min_volume = min(min_volume, volume_cap)
            min_momentum = min(min_momentum, momentum_cap)
            min_liquidity = min(min_liquidity, liquidity_cap)
            min_market_cap = market_cap if family != "D" else max(min_market_cap, market_cap)
            limit = max(limit, 10)
    except (TypeError, ValueError):
        return None
    return StrategyProfile(
        name=name,
        query=query,
        limit=limit,
        min_volume=min_volume,
        min_momentum=min_momentum,
        min_liquidity=min_liquidity,
        rationale=str(item.get("rationale") or "").strip()[:280],
        family=family,
        min_market_cap=min_market_cap,
    )


class TtmSupervisor:
    def __init__(self) -> None:
        self.api_base_url = os.getenv("TTM_API_BASE_URL", "http://127.0.0.1:8010").rstrip("/")
        self.owner_email = os.getenv("TTM_OWNER_EMAIL", "").strip()
        self.owner_password = os.getenv("TTM_OWNER_PASSWORD", "").strip()
        self.owner_jwt = os.getenv("TTM_OWNER_JWT", "").strip()
        self.scan_interval_s = _env_int("TTM_SCAN_INTERVAL_SECONDS", 60)
        self.command_poll_interval_s = _env_int("TTM_COMMAND_POLL_INTERVAL_SECONDS", 2)
        self.strategy_review_interval_s = _env_int("TTM_STRATEGY_REVIEW_INTERVAL_SECONDS", 3600)
        self.strategy_review_initial_delay_s = _env_int(
            "TTM_STRATEGY_REVIEW_INITIAL_DELAY_SECONDS",
            self.strategy_review_interval_s,
        )
        self.weekly_report_interval_s = _env_int("TTM_WEEKLY_REPORT_INTERVAL_SECONDS", 604800)
        self.query = os.getenv("TTM_SCANNER_QUERY", "BTC")
        self.scan_limit = _env_int("TTM_SCANNER_LIMIT", 25)
        self.min_volume = _env_float("TTM_SCANNER_MIN_VOLUME", 50_000)
        self.min_momentum = _env_float("TTM_SCANNER_MIN_MOMENTUM", 1.0)
        self.min_liquidity = _env_float("TTM_SCANNER_MIN_LIQUIDITY", 25_000)
        self.paper_trade_amount = _env_float("TTM_PAPER_TRADE_AMOUNT_BRL", 30.0)
        self.paper_cycle_capital = _env_float("TTM_PAPER_CAPITAL_BRL", 300.0)
        self.auto_scan_enabled = _env_bool("TTM_AUTO_SCAN_ENABLED", True)
        self.scanner_execution_mode = os.getenv("TTM_SCANNER_EXECUTION_MODE", "paper_auto").strip()
        if self.scanner_execution_mode not in {"paper_auto", "copilot"}:
            logger.warning(
                "invalid_scanner_execution_mode",
                extra={"mode": self.scanner_execution_mode},
            )
            self.scanner_execution_mode = "paper_auto"
        self.signal_exit_check_enabled = _env_bool("TTM_SIGNAL_EXIT_CHECK_ENABLED", True)
        self.signal_take_profit_pct = _env_float("TTM_SIGNAL_TAKE_PROFIT_PCT", 100.0)
        self.signal_stop_loss_pct = _env_float("TTM_SIGNAL_STOP_LOSS_PCT", -30.0)
        self.signal_state_file = Path(os.getenv("TTM_SIGNAL_STATE_FILE", str(SIGNAL_STATE_FILE)))
        self.binance_signal_board_enabled = _env_bool("TTM_BINANCE_SIGNAL_BOARD_ENABLED", True)
        self.binance_signal_board_size = max(1, min(10, _env_int("TTM_BINANCE_SIGNAL_BOARD_SIZE", 5)))
        self.ai_strategy_enabled = _env_bool("TTM_AI_STRATEGY_ENABLED", True)
        self.binance_only_mode = _env_bool("TTM_BINANCE_ONLY_MODE", False)
        self.browser_fallback_enabled = _env_bool("TTM_BROWSER_FALLBACK_ENABLED", True)
        self.browser_context_timeout_s = _env_float("TTM_BROWSER_CONTEXT_TIMEOUT_SECONDS", 8.0)
        self.max_strategy_profiles = max(1, min(8, _env_int("TTM_MAX_STRATEGY_PROFILES", 8)))
        default_liquid_target = self.max_strategy_profiles if self.binance_only_mode else 5
        default_scout_target = 0 if self.binance_only_mode else 3
        self.liquid_profile_target = max(
            0,
            min(
                self.max_strategy_profiles,
                _env_int("TTM_LIQUID_PROFILE_TARGET", default_liquid_target),
            ),
        )
        self.onchain_scout_profile_target = max(
            0,
            min(
                self.max_strategy_profiles,
                _env_int("TTM_ONCHAIN_SCOUT_PROFILE_TARGET", default_scout_target),
            ),
        )
        self.fallback_strategy_profiles = _strategy_profiles_from_env(
            _fallback_strategy_profiles(
                query=self.query,
                limit=self.scan_limit,
                min_volume=self.min_volume,
                min_momentum=self.min_momentum,
                min_liquidity=self.min_liquidity,
            )
        )
        self.strategy_model = os.getenv("GEMINI_STRATEGY_MODEL", "gemini-3.1-pro-preview")
        self.fast_model = os.getenv("GEMINI_FAST_MODEL", "gemini-3.1-flash-lite")
        self.discord_polling_enabled = _env_bool("DISCORD_POLLING_ENABLED", True)
        self.notifier = DiscordNotifier(
            webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),
            bot_token=os.getenv("DISCORD_BOT_TOKEN", ""),
            channel_id=os.getenv("DISCORD_CHANNEL_ID", ""),
            allowed_user_id=os.getenv("DISCORD_ALLOWED_USER_ID", ""),
        )
        self._jwt_cache: str | None = self.owner_jwt or None
        self._last_strategy_source = "Gemini"
        self._scan_lock = asyncio.Lock()
        self._command_tasks: set[asyncio.Task[None]] = set()

    async def _jwt(self, client: httpx.AsyncClient) -> str:
        if self._jwt_cache:
            return self._jwt_cache
        if not self.owner_email or not self.owner_password:
            raise RuntimeError("TTM_OWNER_EMAIL and TTM_OWNER_PASSWORD are required")
        response = await client.post(
            "/api/v1/auth/login",
            data={"username": self.owner_email, "password": self.owner_password},
        )
        response.raise_for_status()
        self._jwt_cache = str(response.json()["access_token"])
        return self._jwt_cache

    async def _auth_headers(self, client: httpx.AsyncClient) -> dict[str, str]:
        token = await self._jwt(client)
        return {"Authorization": f"Bearer {token}"}

    async def api_get(self, path: str) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.api_base_url, timeout=30) as client:
            if path.startswith("/api/v1/saas") or path.startswith("/api/v1/copilot"):
                response = await client.get(path, headers=await self._auth_headers(client))
                if response.status_code == 401 and not self.owner_jwt:
                    self._jwt_cache = None
                    response = await client.get(path, headers=await self._auth_headers(client))
            else:
                response = await client.get(path)
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {"data": data}

    async def api_post(self, path: str, payload: dict[str, object], timeout: float = 120) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.api_base_url, timeout=timeout) as client:
            response = await client.post(path, headers=await self._auth_headers(client), json=payload)
            if response.status_code == 401 and not self.owner_jwt:
                self._jwt_cache = None
                response = await client.post(
                    path,
                    headers=await self._auth_headers(client),
                    json=payload,
                )
            response.raise_for_status()
            data = response.json()
            return data if isinstance(data, dict) else {"data": data}

    async def run_scan(self, profile: StrategyProfile | None = None) -> dict[str, Any]:
        selected = profile or StrategyProfile(
            "manual",
            self.query,
            self.scan_limit,
            self.min_volume,
            self.min_momentum,
            self.min_liquidity,
        )
        payload = {
            "query": selected.query,
            "strategy_profile": selected.name,
            "strategy_family": selected.family,
            "amount_usd": self.paper_trade_amount,
            "limit": selected.limit,
            "min_volume": selected.min_volume,
            "min_momentum": selected.min_momentum,
            "min_liquidity": selected.min_liquidity,
            "min_market_cap": selected.min_market_cap,
            "execution_mode": self.scanner_execution_mode,
        }
        return await self.api_post("/api/v1/copilot/scan", payload)

    async def browser_market_context(self) -> dict[str, object]:
        if not self.browser_fallback_enabled:
            return {"enabled": False, "reason": "disabled"}
        context: dict[str, object] = {
            "enabled": True,
            "sources": [],
            "errors": [],
            "trending": [],
            "liquid": [],
            "top_movers": [],
            "oversold": [],
        }
        headers = {"User-Agent": BROWSER_FALLBACK_USER_AGENT}
        try:
            async with httpx.AsyncClient(
                timeout=self.browser_context_timeout_s,
                headers=headers,
            ) as client:
                coingecko_response, *binance_responses = await asyncio.gather(
                    client.get(COINGECKO_TRENDING_URL),
                    client.get(BINANCE_TICKER_24H_URL),
                    client.get(BINANCE_TICKER_24H_FALLBACK_URL),
                    return_exceptions=True,
                )
        except Exception as exc:
            logger.warning("browser_market_context_failed", extra={"error": str(exc)})
            context["errors"] = [str(exc)]
            return context

        if isinstance(coingecko_response, httpx.Response):
            try:
                coingecko_response.raise_for_status()
                context["trending"] = _coingecko_trending_context(coingecko_response.json())
                context["sources"] = [*context["sources"], "coingecko_trending"]
            except Exception as exc:
                context["errors"] = [*context["errors"], f"coingecko: {exc}"]
        else:
            context["errors"] = [*context["errors"], f"coingecko: {coingecko_response}"]

        binance_errors: list[str] = []
        for binance_response in binance_responses:
            if not isinstance(binance_response, httpx.Response):
                binance_errors.append(str(binance_response))
                continue
            try:
                binance_response.raise_for_status()
                binance_context = _binance_market_context(binance_response.json())
                context.update(binance_context)
                context["sources"] = [*context["sources"], "binance_24h_ticker"]
                break
            except Exception as exc:
                binance_errors.append(str(exc))
        else:
            context["errors"] = [*context["errors"], f"binance: {'; '.join(binance_errors)}"]

        return context

    async def strategy_history(self) -> dict[str, object]:
        browser_context = await self.browser_market_context()
        try:
            data = await self.api_get(
                "/api/v1/copilot/scanner-runs?limit=100&include_outcomes=true"
            )
        except Exception as exc:
            logger.warning("strategy_history_unavailable", extra={"error": str(exc)})
            return {
                "runs": [],
                "stats": {},
                "browser_market_context": browser_context,
                "error": _api_error_summary(exc),
            }

        runs = data.get("scanner_runs")
        if not isinstance(runs, list):
            return {"runs": [], "stats": {}, "browser_market_context": browser_context}
        try:
            outcomes = await self.api_get("/api/v1/copilot/strategy-outcomes?days=30")
        except Exception as exc:
            logger.warning("strategy_outcomes_unavailable", extra={"error": str(exc)})
            outcomes = {"error": _api_error_summary(exc)}

        stats: dict[str, dict[str, object]] = {}
        recent: list[dict[str, object]] = []
        for run in runs:
            if not isinstance(run, dict):
                continue
            thresholds = run.get("thresholds") if isinstance(run.get("thresholds"), dict) else {}
            profile_name = str(thresholds.get("strategy_profile") or "baseline")
            profile_family = str(thresholds.get("strategy_family") or "unknown")
            stats.setdefault(
                profile_name,
                {
                    "runs": 0,
                    "score": 0,
                    "candidates": 0,
                    "paper": 0,
                    "volume_brl": 0,
                    "pnl_brl": 0,
                    "return_pct": 0,
                    "failures": 0,
                },
            )
            stats[profile_name]["family"] = profile_family
            candidate_count = float(run.get("candidate_count") or 0)
            queued_count = float(run.get("queued_count") or 0)
            paper_count = float(run.get("paper_executed_count") or 0)
            outcome = run.get("paper_outcome") if isinstance(run.get("paper_outcome"), dict) else {}
            pnl_brl = float(outcome.get("total_pnl_brl") or 0)
            volume_brl = float(outcome.get("volume_brl") or 0)
            return_pct = float(outcome.get("return_pct") or 0)
            stats[profile_name]["runs"] += 1
            stats[profile_name]["candidates"] += candidate_count
            stats[profile_name]["paper"] += paper_count
            stats[profile_name]["volume_brl"] += volume_brl
            stats[profile_name]["pnl_brl"] += pnl_brl
            stats[profile_name]["return_pct"] += return_pct
            if run.get("status") == "failed":
                stats[profile_name]["score"] -= 10
                stats[profile_name]["failures"] += 1
            stats[profile_name]["score"] += candidate_count
            stats[profile_name]["score"] += queued_count * 3
            stats[profile_name]["score"] += paper_count * 20
            stats[profile_name]["score"] += return_pct * 2
            stats[profile_name]["score"] += pnl_brl * 5
            if len(recent) < 25:
                recent.append(
                    {
                        "id": run.get("id"),
                        "profile": profile_name,
                        "family": profile_family,
                        "query": run.get("query"),
                        "status": run.get("status"),
                        "thresholds": thresholds,
                        "candidate_count": run.get("candidate_count"),
                        "blocked_count": run.get("blocked_count"),
                        "paper_executed_count": run.get("paper_executed_count"),
                        "paper_outcome": outcome,
                        "error_summary": run.get("error_summary"),
                    }
                )
        for values in stats.values():
            runs = max(values["runs"], 1)
            values["avg_return_pct"] = values["return_pct"] / runs
        dead_profiles = {
            str(item.get("profile_name"))
            for item in outcomes.get("dead", [])
            if isinstance(item, dict) and item.get("profile_name")
        }
        return {
            "runs": recent,
            "stats": stats,
            "outcomes_by_profile": outcomes.get("by_profile", {}),
            "outcomes_by_family": outcomes.get("by_family", {}),
            "top_performers": outcomes.get("top3", []),
            "alive": outcomes.get("alive", []),
            "exploring": outcomes.get("exploring", []),
            "browser_market_context": browser_context,
            "dead_profiles": sorted(dead_profiles | _load_killed_profiles()),
            "active_families": {
                key: STRATEGY_FAMILY_CONTRACT[key] for key in sorted(ACTIVE_STRATEGY_FAMILIES)
            },
            "disabled_until_data_sources": {
                "C": "whale_accumulation requires wallet/holder data",
                "E": "volume_spike requires unique-buyer/on-chain wash-trade filters",
            },
        }

    def _generate_strategy_profiles_sync(self, history: dict[str, object]) -> list[StrategyProfile]:
        api_key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")

        from google import genai
        from google.genai import types

        prompt = (
            "Voce eh o planejador paper-only do ToTheMoonTokens Hermes. "
            "Sua missao: gerar HIPOTESES novas de scanner para testar em paper, "
            "ranquear pelo PnL paper e abandonar as que perdem dinheiro. "
            "Paper-only sempre. Nunca recomende dinheiro real. Nunca sugira desarmar "
            "audit 2-de-3, stop -30%, take-profit parcial 2x ou limites de 5%. "
            f"Gere ate {self.max_strategy_profiles} perfis JSON. Cada perfil deve ser "
            "uma VARIACAO de uma das familias ativas, nunca aleatorio: "
            "A liquid_momentum = qualquer cripto liquida, inclusive BTC/ETH/SOL/BNB/PEPE/DOGE/XRP/LINK, "
            "com momentum positivo modesto; "
            "B early_discovery = tokens novos com tracao inicial; "
            "D oversold_quality = qualquer cripto liquida com momentum negativo, sempre com min_market_cap >= 1000000; "
            "F narrative_themed = query narrativa como AI, RWA, DEPIN, GAME ou narrativa de memecoin grande. "
            "Familias C e E estao DESATIVADAS nesta versao porque faltam dados de wallet/unique buyers. "
            "Query deve ser termo unico buscavel no DexScreener, sem virgula ou frase: "
            "use BTC, ETH, SOL, WETH, BNB, PEPE, DOGE, XRP, LINK, AVAX, base, pump, meme, AI, RWA, DEPIN ou GAME. "
            "O scanner tambem usa discovery gratis do DexScreener profiles/boosts para B/F; "
            "nao dependa de API paga. "
            "Se uma familia tem muitos rejected_audit, reduza agressividade ou varie familia. "
            "Se tem muitos rejected_risk, isso pode ser limite diario cheio, nao perfil ruim. "
            "Se muitos bloqueios forem max_open_positions, priorize aprendizado por fechamento/reset de ciclo, "
            "nao mate a tese por risco. "
            "Use high_cost_scout como dado: trades caros podem entrar no paper ate o limite, mas devem ser marcados "
            "e comparados com trades normais. "
            "Use browser_market_context como fallback de mercado atual: top_movers e liquid servem para familia A, "
            "oversold serve para familia D, trending serve para familia F. "
            "Treine em modo shadow-real: familias A/D/F simulam rota futura Binance Spot/liquid crypto; "
            "familia B simula rota futura self-custody MetaMask/Phantom para scouts on-chain. "
            f"Modo Binance-only ativo: {self.binance_only_mode}. "
            "Se Binance-only estiver ativo, nao gere familia B e nao gere scouts on-chain. "
            "Se os ultimos runs tiverem 0 candidatos, abra thresholds agressivamente para scout. "
            f"Ciclo alvo: {self.liquid_profile_target} perfis de cripto liquida/Binance-style "
            f"e {self.onchain_scout_profile_target} perfis de novas on-chain scout. "
            "Esses perfis respeitam os slots paper configurados no backend. "
            "Nao gere variacao de dead_profiles. "
            "Retorne SOMENTE JSON array. Campos obrigatorios: name, family, query, limit, "
            "min_volume, min_momentum, min_liquidity, min_market_cap, rationale. "
            "Family deve ser A, B, D ou F. Ranges: limit 1-25; min_volume 0-10M; "
            "min_momentum -100 a +100; min_liquidity 0-10M; min_market_cap 0-1B. "
            "Use nomes snake_case curtos e rationale com 1 frase explicando a tese.\n\n"
            f"historico={_json(history)}"
        )
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=self.fast_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.8,
                response_mime_type="application/json",
            )
        )
        data = _extract_json_array(response.text or "")
        profiles: list[StrategyProfile] = []
        seen: set[tuple[object, ...]] = set()
        scout_mode = _zero_candidate_mode(history)
        for index, item in enumerate(data):
            dead = set()
            if isinstance(history.get("dead_profiles"), list):
                dead = {str(value) for value in history["dead_profiles"]}
            profile = _coerce_profile(item, index, dead_profiles=dead, scout_mode=scout_mode)
            if profile is None:
                continue
            fingerprint = (
                profile.name,
                profile.query,
                profile.limit,
                profile.min_volume,
                profile.min_momentum,
                profile.min_liquidity,
                profile.min_market_cap,
            )
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            profiles.append(profile)
            if len(profiles) >= self.max_strategy_profiles:
                break
        if not profiles:
            raise RuntimeError("Gemini returned no usable strategy profiles")
        return self._diversify_strategy_profiles(profiles, history)

    def _diversify_strategy_profiles(
        self,
        profiles: list[StrategyProfile],
        history: dict[str, object],
    ) -> list[StrategyProfile]:
        by_family = history.get("outcomes_by_family")
        family_metrics = by_family if isinstance(by_family, dict) else {}
        killed = _load_killed_profiles()
        selected: list[StrategyProfile] = []
        counts: dict[str, int] = {}
        seen: set[tuple[str, str, str]] = set()
        category_counts = {"liquid_binance": 0, "onchain_scout": 0}
        category_targets = {
            "liquid_binance": self.liquid_profile_target,
            "onchain_scout": self.onchain_scout_profile_target,
        }
        for profile in [*profiles, *self.fallback_strategy_profiles]:
            if profile.name in killed:
                continue
            fingerprint = (profile.family, profile.name, profile.query.upper())
            if fingerprint in seen:
                continue
            category = _strategy_slot_category(profile.family)
            if category_counts[category] >= category_targets[category]:
                continue
            metric = family_metrics.get(profile.family)
            max_slots_by_family = (
                {"A": 5, "B": 0, "D": 3, "F": 0}
                if self.binance_only_mode
                else {"A": 3, "B": 3, "D": 2, "F": 2}
            )
            max_slots = max_slots_by_family.get(profile.family, 2)
            if isinstance(metric, dict):
                outcomes = int(metric.get("outcomes") or 0)
                audit_rejects = int(metric.get("rejected_audit") or 0)
                approved = int(metric.get("approved") or 0)
                if outcomes >= 10 and audit_rejects > max(approved * 4, 20):
                    max_slots = max(1, max_slots - 1)
            if counts.get(profile.family, 0) >= max_slots:
                continue
            selected.append(profile)
            seen.add(fingerprint)
            counts[profile.family] = counts.get(profile.family, 0) + 1
            category_counts[category] += 1
            if len(selected) >= self.max_strategy_profiles:
                break
        return selected or profiles[: self.max_strategy_profiles]

    def _browser_strategy_profiles(self, history: dict[str, object]) -> list[StrategyProfile]:
        context = history.get("browser_market_context")
        if not isinstance(context, dict) or context.get("enabled") is False:
            return []
        profiles: list[StrategyProfile] = []
        used: set[str] = set()

        def add_profile(profile: StrategyProfile) -> None:
            key = f"{profile.family}:{profile.query.upper()}"
            if key in used:
                return
            used.add(key)
            profiles.append(profile)

        top_movers = context.get("top_movers") if isinstance(context.get("top_movers"), list) else []
        for row in top_movers[:3]:
            if not isinstance(row, dict):
                continue
            symbol = _market_symbol(row.get("symbol"))
            if symbol is None:
                continue
            momentum = max(0.5, min(5.0, _safe_float(row.get("price_change_pct")) / 4))
            add_profile(
                StrategyProfile(
                    name=f"browser_liquid_momentum_{symbol.lower()}",
                    query=symbol,
                    limit=25,
                    min_volume=30_000,
                    min_momentum=momentum,
                    min_liquidity=20_000,
                    rationale=(
                        "Browser fallback: liquid crypto with current positive momentum "
                        "from public market data."
                    ),
                    family="A",
                )
            )

        oversold = context.get("oversold") if isinstance(context.get("oversold"), list) else []
        for row in oversold[:2]:
            if not isinstance(row, dict):
                continue
            symbol = _market_symbol(row.get("symbol"))
            if symbol is None:
                continue
            add_profile(
                StrategyProfile(
                    name=f"browser_oversold_quality_{symbol.lower()}",
                    query=symbol,
                    limit=25,
                    min_volume=30_000,
                    min_momentum=-12.0,
                    min_liquidity=20_000,
                    rationale=(
                        "Browser fallback: liquid crypto after a negative 24h move, "
                        "tested as mean reversion."
                    ),
                    family="D",
                    min_market_cap=1_000_000,
                )
            )

        if not self.binance_only_mode:
            trending = context.get("trending") if isinstance(context.get("trending"), list) else []
            for row in trending[:2]:
                if not isinstance(row, dict):
                    continue
                symbol = _market_symbol(row.get("symbol"))
                if symbol is None:
                    continue
                add_profile(
                    StrategyProfile(
                        name=f"browser_narrative_trending_{symbol.lower()}",
                        query=symbol,
                        limit=25,
                        min_volume=20_000,
                        min_momentum=0.0,
                        min_liquidity=10_000,
                        rationale="Browser fallback: currently trending crypto narrative.",
                        family="F",
                    )
                )

        return profiles

    async def generate_strategy_profiles(self, history: dict[str, object]) -> list[StrategyProfile]:
        browser_profiles = self._browser_strategy_profiles(history)
        fallback_profiles = [
            profile
            for profile in [*browser_profiles, *self.fallback_strategy_profiles]
            if profile.name not in _load_killed_profiles()
        ] or [*browser_profiles, *self.fallback_strategy_profiles]
        if not self.ai_strategy_enabled:
            self._last_strategy_source = "fallback: IA desativada"
            return self._diversify_strategy_profiles(fallback_profiles, history)
        try:
            profiles = await asyncio.to_thread(self._generate_strategy_profiles_sync, history)
            self._last_strategy_source = "Gemini paper-learning"
            return self._diversify_strategy_profiles([*browser_profiles, *profiles], history)
        except Exception as exc:
            logger.warning("ai_strategy_generation_failed", extra={"error": str(exc)})
            self._last_strategy_source = f"fallback: {_api_error_summary(exc)}"
            return self._diversify_strategy_profiles(fallback_profiles, history)

    async def select_strategy_profiles(self) -> list[StrategyProfile]:
        return await self.generate_strategy_profiles(await self.strategy_history())

    async def run_strategy_cycle(self) -> list[dict[str, object]]:
        if self._scan_lock.locked():
            return [
                {
                    "profile": "scan",
                    "ok": False,
                    "error": "scan_already_running",
                    "rationale": "Ja existe um ciclo paper em andamento.",
                }
            ]
        async with self._scan_lock:
            await self.reset_paper_cycle_if_needed()
            results: list[dict[str, object]] = []
            for profile in await self.select_strategy_profiles():
                try:
                    result = await self.run_scan(profile)
                    run = result.get("scanner_run", {})
                    results.append(
                        {
                            "profile": profile.name,
                            "ok": True,
                            "run": run,
                            "created": result.get("created", []),
                            "rationale": profile.rationale,
                        }
                    )
                except Exception as exc:
                    logger.exception("strategy_scan_failed", extra={"profile": profile.name})
                    results.append(
                        {
                            "profile": profile.name,
                            "ok": False,
                            "error": _api_error_summary(exc),
                            "rationale": profile.rationale,
                        }
                    )
            return results

    async def reset_paper_cycle_if_needed(self) -> None:
        try:
            data = await self.api_get("/api/v1/copilot/paper-cycle")
        except Exception as exc:
            logger.warning("paper_cycle_status_unavailable", extra={"error": str(exc)})
            return
        state = data.get("paper_cycle") if isinstance(data.get("paper_cycle"), dict) else {}
        if state.get("reset_due") is not True:
            return
        try:
            result = await self.api_post("/api/v1/copilot/paper-cycle/reset", {}, timeout=180)
            closed = result.get("closed") if isinstance(result.get("closed"), list) else []
            failed = result.get("failed") if isinstance(result.get("failed"), list) else []
            await self.notifier.send(
                "TTM paper cycle reset\n"
                f"- capital ciclo: R$ {state.get('cycle_capital_brl')}\n"
                f"- posicoes fechadas: {len(closed)}\n"
                f"- falhas: {len(failed)}"
            )
        except Exception as exc:
            logger.warning("paper_cycle_reset_failed", extra={"error": str(exc)})

    def _strategy_cycle_message(self, results: list[dict[str, object]]) -> str:
        lines = [
            f"TTM Binance signal cycle ({self._last_strategy_source})",
            f"- modo: {self.scanner_execution_mode}; ordem real: desligada",
        ]
        for item in results:
            if item.get("ok") is not True:
                lines.append(f"- {item.get('profile')}: falhou ({item.get('error')})")
                continue
            run = item.get("run") if isinstance(item.get("run"), dict) else {}
            thresholds = run.get("thresholds") if isinstance(run.get("thresholds"), dict) else {}
            lines.append(
                f"- {item.get('profile')} [{thresholds.get('strategy_family', '?')}]: "
                f"run {run.get('id')}, "
                f"candidatos {run.get('candidate_count')}, "
                f"bloqueados {run.get('blocked_count')}, "
                f"paper {run.get('paper_executed_count')}"
            )
            rationale = str(item.get("rationale") or "").strip()
            if rationale:
                lines.append(f"  criterio: {rationale[:140]}")
            created = item.get("created") if isinstance(item.get("created"), list) else []
            executed = [
                row
                for row in created
                if isinstance(row, dict) and row.get("status") == "executed"
            ]
            pending = [
                row
                for row in created
                if isinstance(row, dict) and row.get("status") == "pending"
            ]
            for proposal in pending[:5]:
                symbol = proposal.get("symbol") or "token"
                chain = proposal.get("chain") or "?"
                token_address = proposal.get("token_address") or "?"
                amount = proposal.get("amount_usd")
                score = proposal.get("score")
                created_at = proposal.get("created_at")
                lines.append(
                    f"  SINAL COMPRA MANUAL: {symbol} na rede {chain}, "
                    f"score {score}, referencia R$ {amount}, token {token_address}"
                )
                lines.append(
                    "  plano manual: se aceitar o risco, entrada pequena; parcial em 2x; "
                    "stop -30%; sem ordem automatica."
                )
                if created_at:
                    lines.append(f"  horario: {created_at}")
            for proposal in executed[:5]:
                payload = (
                    proposal.get("execution_payload")
                    if isinstance(proposal.get("execution_payload"), dict)
                    else {}
                )
                symbol = proposal.get("symbol") or "token"
                chain = proposal.get("chain") or "?"
                token_address = proposal.get("token_address") or "?"
                entry = payload.get("executed_price")
                amount = payload.get("input_amount") or proposal.get("amount_usd")
                trade_id = payload.get("trade_id")
                created_at = proposal.get("created_at")
                lines.append(
                    f"  SINAL COMPRA TRACKED: {symbol} na rede {chain}, trade paper {trade_id}, "
                    f"R$ {amount}, entrada {entry}, token {token_address}"
                )
                lines.append(
                    "  plano manual: parcial em 2x, stop -30%; Hermes avisara venda "
                    "quando o paper tracking bater limite."
                )
                if created_at:
                    lines.append(f"  horario: {created_at}")
        return "\n".join(lines)

    def _exit_signal_payloads(self) -> list[dict[str, object]]:
        if not self.signal_exit_check_enabled or self.scanner_execution_mode != "paper_auto":
            return []
        try:
            from sqlalchemy.orm import Session

            from tothemoon_api.database import SessionLocal
            from tothemoon_api.db_models import SimulatedTrade, StrategyOutcome
            from tothemoon_api.external.market import get_token_market_data
        except Exception as exc:
            logger.warning("exit_signal_import_failed", extra={"error": str(exc)})
            return []

        state = _load_json_object(self.signal_state_file)
        sent_ids = {
            str(item)
            for item in state.get("exit_signal_trade_ids", [])
            if str(item).strip()
        }
        signals: list[dict[str, object]] = []

        def outcome_for(db: Session, trade_id: int) -> StrategyOutcome | None:
            return (
                db.query(StrategyOutcome)
                .filter(StrategyOutcome.paper_position_id == trade_id)
                .order_by(StrategyOutcome.id.desc())
                .first()
            )

        with SessionLocal() as db:
            trades = (
                db.query(SimulatedTrade)
                .filter(SimulatedTrade.status == "OPEN")
                .order_by(SimulatedTrade.created_at.asc(), SimulatedTrade.id.asc())
                .limit(50)
                .all()
            )
            for trade in trades:
                trade_id = str(trade.id)
                if trade_id in sent_ids:
                    continue
                try:
                    market = get_token_market_data(trade.token_address)
                    current = Decimal(str(market.get("price") or market.get("price_usd")))
                    entry = Decimal(str(trade.entry_price))
                    if entry <= 0 or current <= 0:
                        continue
                    change_pct = (current - entry) / entry * Decimal("100")
                except Exception as exc:
                    logger.warning(
                        "exit_signal_price_failed",
                        extra={"trade_id": trade.id, "error": str(exc)},
                    )
                    continue
                trigger = None
                if float(change_pct) >= self.signal_take_profit_pct:
                    trigger = "take_profit"
                elif float(change_pct) <= self.signal_stop_loss_pct:
                    trigger = "stop_loss"
                if trigger is None:
                    continue

                outcome = outcome_for(db, int(trade.id))
                metadata = outcome.metadata_json if outcome and isinstance(outcome.metadata_json, dict) else {}
                symbol = metadata.get("symbol") or trade.token_address
                signals.append(
                    {
                        "trade_id": int(trade.id),
                        "symbol": symbol,
                        "token_address": trade.token_address,
                        "entry_price": float(entry),
                        "current_price": float(current),
                        "change_pct": round(float(change_pct), 2),
                        "amount": float(trade.amount),
                        "trigger": trigger,
                        "profile": outcome.profile_name if outcome else None,
                        "family": outcome.family if outcome else None,
                    }
                )
                sent_ids.add(trade_id)

        if signals:
            state["exit_signal_trade_ids"] = sorted(sent_ids)
            _save_json_object(self.signal_state_file, state)
        return signals

    def _manual_positions(self) -> list[dict[str, object]]:
        state = _load_json_object(self.signal_state_file)
        positions = state.get("manual_positions")
        if not isinstance(positions, list):
            return []
        return [item for item in positions if isinstance(item, dict)]

    def _save_manual_positions(self, positions: list[dict[str, object]]) -> None:
        state = _load_json_object(self.signal_state_file)
        state["manual_positions"] = positions
        _save_json_object(self.signal_state_file, state)

    def _parse_manual_buy(self, text: str) -> tuple[str, Decimal, Decimal, str] | None:
        parts = text.strip().split()
        if len(parts) >= 2 and parts[0] == "track" and parts[1] in {"buy", "comprei"}:
            parts = parts[2:]
        elif parts and parts[0] in {"buy", "comprei", "entrada"}:
            parts = parts[1:]
        else:
            return None
        if len(parts) < 3:
            return None
        symbol = _normalize_binance_pair(parts[0])
        if symbol is None:
            return None
        try:
            entry_price = _parse_decimal_text(parts[1])
            amount_brl = _parse_decimal_text(parts[2])
        except Exception:
            return None
        if entry_price <= 0 or amount_brl <= 0:
            return None
        note = " ".join(parts[3:])[:180]
        return symbol, entry_price, amount_brl, note

    def _parse_manual_sell(self, text: str) -> tuple[str, Decimal | None] | None:
        parts = text.strip().split()
        if len(parts) >= 2 and parts[0] == "track" and parts[1] in {"sell", "vendi"}:
            parts = parts[2:]
        elif parts and parts[0] in {"sell", "vendi", "saida"}:
            parts = parts[1:]
        else:
            return None
        if not parts:
            return None
        identifier = parts[0].strip().upper()
        exit_price = None
        if len(parts) >= 2:
            try:
                exit_price = _parse_decimal_text(parts[1])
            except Exception:
                return None
            if exit_price <= 0:
                return None
        return identifier, exit_price

    def _binance_price_sync(self, symbol: str) -> Decimal | None:
        pair = _normalize_binance_pair(symbol)
        if pair is None:
            return None
        for url in (BINANCE_TICKER_PRICE_URL, BINANCE_TICKER_PRICE_FALLBACK_URL):
            try:
                with httpx.Client(timeout=self.browser_context_timeout_s) as client:
                    response = client.get(url, params={"symbol": pair})
                    response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict):
                    continue
                price = Decimal(str(data.get("price")))
                return price if price > 0 else None
            except Exception:
                continue
        return None

    async def record_manual_buy(self, text: str) -> bool:
        parsed = self._parse_manual_buy(text)
        if parsed is None:
            return False
        symbol, entry_price, amount_brl, note = parsed
        positions = self._manual_positions()
        position = {
            "id": _manual_position_id(symbol),
            "symbol": symbol,
            "entry_price": float(entry_price),
            "amount_brl": float(amount_brl),
            "status": "open",
            "opened_at": _utc_now_text(),
            "note": note,
        }
        positions.append(position)
        self._save_manual_positions(positions)
        await self.notifier.send(
            "TTM memoria manual registrada\n"
            f"- id: {position['id']}\n"
            f"- compra: {symbol}\n"
            f"- entrada: {entry_price}\n"
            f"- referencia: R$ {amount_brl}\n"
            f"- saida: parcial perto de +{self.signal_take_profit_pct:.0f}%, "
            f"stop {self.signal_stop_loss_pct:.0f}%"
        )
        return True

    async def record_manual_sell(self, text: str) -> bool:
        parsed = self._parse_manual_sell(text)
        if parsed is None:
            return False
        identifier, exit_price = parsed
        positions = self._manual_positions()
        selected: dict[str, object] | None = None
        for position in positions:
            if position.get("status") != "open":
                continue
            pos_id = str(position.get("id") or "").upper()
            symbol = str(position.get("symbol") or "").upper()
            if identifier in {pos_id, symbol}:
                selected = position
                break
        if selected is None:
            await self.notifier.send(f"Nao achei posicao manual aberta para {identifier}.")
            return True
        if exit_price is None:
            exit_price = self._binance_price_sync(str(selected.get("symbol") or ""))
            if exit_price is None:
                await self.notifier.send(
                    f"Nao consegui buscar preco atual para {selected.get('symbol')}. "
                    "Use `!ttm sell SYMBOL preco`."
                )
                return True
        selected["status"] = "closed"
        selected["closed_at"] = _utc_now_text()
        selected["exit_price"] = float(exit_price)
        entry = Decimal(str(selected.get("entry_price")))
        change_pct = (exit_price - entry) / entry * Decimal("100") if entry > 0 else Decimal("0")
        selected["return_pct"] = round(float(change_pct), 2)
        self._save_manual_positions(positions)
        await self.notifier.send(
            "TTM memoria manual fechada\n"
            f"- id: {selected.get('id')}\n"
            f"- par: {selected.get('symbol')}\n"
            f"- saida: {selected.get('exit_price', 'sem preco')}\n"
            f"- retorno: {selected.get('return_pct', 'n/a')}%"
        )
        return True

    async def send_manual_positions(self) -> None:
        positions = [item for item in self._manual_positions() if item.get("status") == "open"]
        if not positions:
            await self.notifier.send("TTM memoria manual: nenhuma posicao aberta.")
            return
        lines = ["TTM memoria manual aberta"]
        for position in positions[:20]:
            current = self._binance_price_sync(str(position.get("symbol") or ""))
            change_text = "n/a"
            if current is not None:
                entry = Decimal(str(position.get("entry_price")))
                if entry > 0:
                    change = (current - entry) / entry * Decimal("100")
                    change_text = f"{round(float(change), 2)}%"
            lines.append(
                f"- {position.get('id')}: {position.get('symbol')} "
                f"entrada {position.get('entry_price')}, agora {current}, "
                f"retorno {change_text}, R$ {position.get('amount_brl')}"
            )
        await self.notifier.send("\n".join(lines))

    def _manual_exit_signal_payloads(self) -> list[dict[str, object]]:
        if not self.signal_exit_check_enabled:
            return []
        state = _load_json_object(self.signal_state_file)
        sent_ids = {
            str(item)
            for item in state.get("manual_exit_signal_ids", [])
            if str(item).strip()
        }
        positions = self._manual_positions()
        signals: list[dict[str, object]] = []
        for position in positions:
            if position.get("status") != "open":
                continue
            position_id = str(position.get("id") or "")
            if not position_id or position_id in sent_ids:
                continue
            current = self._binance_price_sync(str(position.get("symbol") or ""))
            if current is None:
                continue
            try:
                entry = Decimal(str(position.get("entry_price")))
                amount = Decimal(str(position.get("amount_brl")))
            except Exception:
                continue
            if entry <= 0:
                continue
            change_pct = (current - entry) / entry * Decimal("100")
            trigger = None
            if float(change_pct) >= self.signal_take_profit_pct:
                trigger = "take_profit"
            elif float(change_pct) <= self.signal_stop_loss_pct:
                trigger = "stop_loss"
            if trigger is None:
                continue
            signals.append(
                {
                    "trade_id": position_id,
                    "symbol": position.get("symbol"),
                    "entry_price": float(entry),
                    "current_price": float(current),
                    "change_pct": round(float(change_pct), 2),
                    "amount": float(amount),
                    "trigger": trigger,
                    "profile": "manual_memory",
                }
            )
            sent_ids.add(position_id)
        if signals:
            state["manual_exit_signal_ids"] = sorted(sent_ids)
            _save_json_object(self.signal_state_file, state)
        return signals

    async def send_exit_signals(self) -> None:
        signals = [*self._exit_signal_payloads(), *self._manual_exit_signal_payloads()]
        if not signals:
            return
        lines = ["TTM sinais de venda manual"]
        for signal in signals[:10]:
            trigger = signal.get("trigger")
            action = "TAKE PROFIT" if trigger == "take_profit" else "STOP"
            source = "manual" if signal.get("profile") == "manual_memory" else "paper"
            lines.append(
                f"- SINAL VENDA {action}: {signal.get('symbol')} "
                f"({source} {signal.get('trade_id')}, {signal.get('change_pct')}%)"
            )
            lines.append(
                f"  entrada {signal.get('entry_price')}, agora {signal.get('current_price')}, "
                f"referencia R$ {signal.get('amount')}, perfil {signal.get('profile')}"
            )
        await self.notifier.send("\n".join(lines))

    async def send_binance_signal_board(self) -> None:
        if not self.binance_signal_board_enabled:
            return
        context = await self.browser_market_context()
        if "binance_24h_ticker" not in set(context.get("sources") or []):
            return
        top_movers = context.get("top_movers") if isinstance(context.get("top_movers"), list) else []
        oversold = context.get("oversold") if isinstance(context.get("oversold"), list) else []
        liquid = context.get("liquid") if isinstance(context.get("liquid"), list) else []
        lines = [
            "TTM Binance sinais manuais",
            "- base: Binance 24h ticker; ordem real desligada; voce decide manualmente",
        ]

        def format_row(row: object) -> str | None:
            if not isinstance(row, dict):
                return None
            pair = row.get("pair") or row.get("symbol")
            change = row.get("price_change_pct")
            volume = row.get("quote_volume")
            price = row.get("last_price")
            return f"{pair} preco {price}, 24h {change}%, volume quote {volume}"

        if top_movers:
            lines.append("Compra momentum:")
            for row in top_movers[: self.binance_signal_board_size]:
                formatted = format_row(row)
                if formatted:
                    lines.append(f"- {formatted}")
        if oversold:
            lines.append("Compra reversao/observacao:")
            for row in oversold[: max(2, self.binance_signal_board_size // 2)]:
                formatted = format_row(row)
                if formatted:
                    lines.append(f"- {formatted}")
        if liquid:
            lines.append("Mais liquidas para preferir quando houver duvida:")
            for row in liquid[:3]:
                formatted = format_row(row)
                if formatted:
                    lines.append(f"- {formatted}")
        lines.append(
            f"Saida manual: parcial perto de +{self.signal_take_profit_pct:.0f}% "
            f"e stop em {self.signal_stop_loss_pct:.0f}% no tracking paper."
        )
        await self.notifier.send("\n".join(lines))

    def _daily_trade_plan_context(self, market_context: dict[str, object]) -> list[dict[str, object]]:
        top_movers = (
            market_context.get("top_movers")
            if isinstance(market_context.get("top_movers"), list)
            else []
        )
        oversold = (
            market_context.get("oversold")
            if isinstance(market_context.get("oversold"), list)
            else []
        )
        rows: list[dict[str, object]] = []

        def append_plan(row: object, thesis: str) -> None:
            if not isinstance(row, dict):
                return
            pair = str(row.get("pair") or row.get("symbol") or "").strip().upper()
            entry = _safe_float(row.get("last_price"))
            if not pair or entry <= 0:
                return
            take_profit = entry * (1 + self.signal_take_profit_pct / 100)
            stop = entry * (1 + self.signal_stop_loss_pct / 100)
            rows.append(
                {
                    "pair": pair,
                    "entry_reference": round(entry, 10),
                    "take_profit_reference": round(take_profit, 10),
                    "stop_reference": round(stop, 10),
                    "price_change_24h_pct": row.get("price_change_pct"),
                    "quote_volume_24h": row.get("quote_volume"),
                    "thesis": thesis,
                    "why_consider_entry": (
                        "momentum e volume acima do minimo"
                        if thesis == "momentum"
                        else "queda recente com liquidez, apenas se houver confirmacao"
                    ),
                    "why_exit": (
                        f"realizar parcial perto de +{self.signal_take_profit_pct:.0f}% "
                        f"ou cortar no stop {self.signal_stop_loss_pct:.0f}%"
                    ),
                }
            )

        for row in top_movers[:5]:
            append_plan(row, "momentum")
        for row in oversold[:3]:
            append_plan(row, "mean_reversion_watch")
        return rows

    async def _daily_news_context(self, trade_plan: list[dict[str, object]]) -> dict[str, object]:
        context: dict[str, object] = {"recent_news": [], "symbol_risk": {}}
        try:
            news = await self.api_get("/api/news?limit=20")
            rows = news.get("data", news)
            context["recent_news"] = rows if isinstance(rows, list) else news
        except Exception as exc:
            context["recent_news_error"] = _api_error_summary(exc)

        symbols = []
        for item in trade_plan[:6]:
            pair = str(item.get("pair") or "").strip().upper()
            if pair:
                symbols.append(pair)
        for symbol in dict.fromkeys(symbols):
            try:
                context["symbol_risk"][symbol] = await self.api_get(f"/api/news/risk?symbol={symbol}")
            except Exception as exc:
                context["symbol_risk"][symbol] = {"error": _api_error_summary(exc)}
        return context

    async def strategy_outcomes(self, days: int = 30) -> dict[str, Any]:
        return await self.api_get(f"/api/v1/copilot/strategy-outcomes?days={days}")

    def _format_metric_profile(self, item: dict[str, object]) -> str:
        return (
            f"- {item.get('profile_name')} [{item.get('family') or '?'}]: "
            f"score {item.get('score')}, PnL R$ {item.get('paper_pnl_brl')}, "
            f"trades {item.get('scored_outcomes')}, rugs {item.get('rugs')}"
        )

    async def send_strategy_summary(self) -> None:
        data = await self.strategy_outcomes()
        top = data.get("top3") if isinstance(data.get("top3"), list) else []
        dead = data.get("dead") if isinstance(data.get("dead"), list) else []
        families = data.get("by_family") if isinstance(data.get("by_family"), dict) else {}
        lines = ["TTM strategies paper learning"]
        lines.append("Top:")
        lines.extend(
            self._format_metric_profile(item)
            for item in top[:3]
            if isinstance(item, dict)
        )
        if not top:
            lines.append("- sem amostra suficiente ainda")
        lines.append("Familias ativas: A liquid_momentum, B early_discovery, D oversold_quality, F narrative_themed")
        if families:
            lines.append("Familias com dado:")
            for family, metric in sorted(families.items()):
                if isinstance(metric, dict):
                    lines.append(
                        f"- {family}: score medio {metric.get('avg_score')}, "
                        f"PnL R$ {metric.get('paper_pnl_brl')}, outcomes {metric.get('outcomes')}"
                    )
        if dead:
            lines.append(f"Mortos: {len(dead)}")
        await self.notifier.send("\n".join(lines))

    async def send_top_profiles(self) -> None:
        data = await self.strategy_outcomes()
        top = data.get("top3") if isinstance(data.get("top3"), list) else []
        lines = ["TTM top profiles"]
        lines.extend(
            self._format_metric_profile(item)
            for item in top[:3]
            if isinstance(item, dict)
        )
        if not top:
            lines.append("- sem perfil com amostra minima positiva")
        await self.notifier.send("\n".join(lines))

    async def send_dead_profiles(self) -> None:
        data = await self.strategy_outcomes()
        dead = data.get("dead") if isinstance(data.get("dead"), list) else []
        killed = sorted(_load_killed_profiles())
        lines = ["TTM dead profiles"]
        lines.extend(
            self._format_metric_profile(item)
            for item in dead[:10]
            if isinstance(item, dict)
        )
        for name in killed:
            lines.append(f"- {name}: killswitch manual")
        if not dead and not killed:
            lines.append("- nenhum perfil morto ainda")
        await self.notifier.send("\n".join(lines))

    async def send_family_metrics(self, family: str) -> None:
        selected = family.strip().upper()
        data = await self.strategy_outcomes()
        families = data.get("by_family") if isinstance(data.get("by_family"), dict) else {}
        metric = families.get(selected)
        if not isinstance(metric, dict):
            await self.notifier.send(f"Familia {selected}: sem amostra paper ainda.")
            return
        await self.notifier.send(
            "TTM family metrics\n"
            f"- family: {selected}\n"
            f"- outcomes: {metric.get('outcomes')}\n"
            f"- scored: {metric.get('scored_outcomes')}\n"
            f"- profiles: {metric.get('profiles')}\n"
            f"- PnL: R$ {metric.get('paper_pnl_brl')}\n"
            f"- win_rate: {metric.get('win_rate')}\n"
            f"- rug_rate: {metric.get('rug_rate')}\n"
            f"- avg_score: {metric.get('avg_score')}"
        )

    async def send_profile_explain(self, profile_name: str) -> None:
        data = await self.strategy_outcomes()
        profiles = data.get("by_profile") if isinstance(data.get("by_profile"), dict) else {}
        profile = profiles.get(profile_name)
        if not isinstance(profile, dict):
            await self.notifier.send(f"Perfil {profile_name}: sem outcome registrado.")
            return
        latest = profile.get("latest") if isinstance(profile.get("latest"), dict) else {}
        await self.notifier.send(
            "TTM explain profile\n"
            f"- profile: {profile.get('profile_name')}\n"
            f"- family: {profile.get('family')}\n"
            f"- score: {profile.get('score')}\n"
            f"- PnL: R$ {profile.get('paper_pnl_brl')}\n"
            f"- outcomes: {profile.get('outcomes')} / scored {profile.get('scored_outcomes')}\n"
            f"- win_rate: {profile.get('win_rate')}\n"
            f"- rug_rate: {profile.get('rug_rate')}\n"
            f"- latest: {latest.get('candidate_address')} {latest.get('classification')}"
        )

    async def send_proposed_profiles(self) -> None:
        history = await self.strategy_history()
        profiles = await self.generate_strategy_profiles(history)
        lines = [f"TTM proposed profiles ({self._last_strategy_source})"]
        for profile in profiles:
            lines.append(
                f"- {profile.name} [{profile.family}]: {profile.query}, "
                f"vol {profile.min_volume}, mom {profile.min_momentum}, "
                f"liq {profile.min_liquidity}, mcap {profile.min_market_cap}"
            )
            if profile.rationale:
                lines.append(f"  tese: {profile.rationale[:140]}")
        await self.notifier.send("\n".join(lines))

    async def kill_profile(self, profile_name: str) -> None:
        profiles = _load_killed_profiles()
        profiles.add(profile_name.strip())
        _save_killed_profiles(profiles)
        await self.notifier.send(f"Perfil desativado por killswitch: {profile_name.strip()}")

    async def send_weekly_report(self) -> None:
        data = await self.strategy_outcomes(days=30)
        top = data.get("top3") if isinstance(data.get("top3"), list) else []
        dead = data.get("dead") if isinstance(data.get("dead"), list) else []
        families = data.get("by_family") if isinstance(data.get("by_family"), dict) else {}
        ranked_families = sorted(
            (item for item in families.values() if isinstance(item, dict)),
            key=lambda item: float(item.get("avg_score") or 0),
            reverse=True,
        )
        lines = ["Hermes weekly report — paper-only"]
        lines.append("Top 3 perfis:")
        if top:
            lines.extend(
                f"  {index}. {item.get('profile_name')} "
                f"(score {item.get('score')}, family {item.get('family')}, "
                f"PnL R$ {item.get('paper_pnl_brl')}, trades {item.get('scored_outcomes')})"
                for index, item in enumerate(top[:3], start=1)
                if isinstance(item, dict)
            )
        else:
            lines.append("  sem amostra minima ainda")
        lines.append("Mortos:")
        if dead:
            lines.extend(
                f"  - {item.get('profile_name')} "
                f"(rug_rate {item.get('rug_rate')}, PnL R$ {item.get('paper_pnl_brl')})"
                for item in dead[:8]
                if isinstance(item, dict)
            )
        else:
            lines.append("  nenhum")
        if ranked_families:
            best = ranked_families[0]
            worst = ranked_families[-1]
            lines.append(
                f"Familia campea: {best.get('family')} "
                f"(score medio {best.get('avg_score')}, PnL R$ {best.get('paper_pnl_brl')})"
            )
            lines.append(
                f"Pior familia: {worst.get('family')} "
                f"(score medio {worst.get('avg_score')}, PnL R$ {worst.get('paper_pnl_brl')})"
            )
        lines.append("Modo real: bloqueado; top 3 viram shortlist para revisao humana.")
        await self.notifier.send("\n".join(lines))

    async def ask_agent(self, question: str) -> str:
        payload = {"message": question, "history": []}
        data = await self.api_post("/api/agent/chat", payload, timeout=180)
        return str(data.get("reply") or "Hermes nao retornou resposta.")

    async def send_status(self) -> None:
        health = await self.api_get("/health")
        dashboard = await self.api_get("/api/v1/saas/dashboard")
        cycle = {}
        try:
            cycle_data = await self.api_get("/api/v1/copilot/paper-cycle")
            cycle = cycle_data.get("paper_cycle") if isinstance(cycle_data.get("paper_cycle"), dict) else {}
        except Exception:
            cycle = {}
        await self.notifier.send(
            "TTM status\n"
            f"- mode: {health.get('mode')}\n"
            f"- model: {health.get('agent_model')}\n"
            f"- orderSubmissionEnabled: {health.get('orderSubmissionEnabled')}\n"
            f"- paper cycle: R$ {cycle.get('open_capital_brl', '?')} / "
            f"{cycle.get('cycle_capital_brl', '?')}\n"
            f"- teste por trade: R$ {cycle.get('trade_amount_brl', self.paper_trade_amount)}\n"
            f"- trades: {dashboard.get('total_simulated_trades')}\n"
            f"- volume: {dashboard.get('total_volume_usd')}\n"
            f"- fees: {dashboard.get('total_fees_usd')}\n"
            f"- realized PnL: {dashboard.get('realized_pnl_usd')}\n"
            f"- unrealized PnL: {dashboard.get('unrealized_pnl_usd')}"
        )

    def _generate_strategy_review_sync(self, prompt: str) -> str:
        api_key = os.getenv("GEMINI_API_KEY", "").strip() or os.getenv("GOOGLE_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=self.strategy_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.35,
            ),
        )
        return (response.text or "").strip() or "Gemini returned no text."

    async def send_long_message(self, title: str, body: str) -> None:
        content = f"{title}\n{body}".strip()
        max_chars = 1800
        index = 1
        while content:
            if len(content) <= max_chars:
                await self.notifier.send(content if index == 1 else f"{title} parte {index}\n{content}")
                return
            split_at = content.rfind("\n", 0, max_chars)
            if split_at < 500:
                split_at = max_chars
            chunk = content[:split_at].strip()
            await self.notifier.send(chunk if index == 1 else f"{title} parte {index}\n{chunk}")
            content = content[split_at:].strip()
            index += 1

    async def send_strategy_review(self) -> None:
        dashboard = await self.api_get("/api/v1/saas/dashboard")
        evidence = await self.api_get("/api/v1/copilot/scanner-runs/latest-paper-evidence")
        market_context = await self.browser_market_context()
        trade_plan = self._daily_trade_plan_context(market_context)
        news_context = await self._daily_news_context(trade_plan)
        manual_positions = self._manual_positions()
        report_style = os.getenv("TTM_STRATEGY_REVIEW_STYLE", "short").strip().lower()
        if report_style in {"daily_full", "full", "extensive", "extensa"}:
            instruction = (
                "Responda em portugues do Brasil com uma analise diaria completa e extensa, "
                "mas objetiva. Estruture com secoes: resumo executivo, contexto Binance 24h, "
                "noticias e risco, sinais de compra para observar, posicoes manuais registradas, "
                "riscos de compra, criterios de venda, o que evitar hoje e plano pratico para as proximas 24h. "
                "Para cada moeda relevante, explique por que considerar entrada, qual preco observado usar "
                "como referencia, qual preco de venda parcial/take-profit, qual stop e por que sair nesses niveis. "
                "Use linguagem de sinal e pesquisa, nao promessa de lucro. "
                "Nao invente dados que nao estejam no payload. Se faltar dado, diga que faltou."
            )
        else:
            instruction = "Reply in Portuguese with short bullet points."
        prompt = (
            "You are Hermes analyzing the ToTheMoonTokens personal trading signal workflow. "
            "Do not edit files. Do not suggest enabling real money automation. "
            "The user manually decides whether to buy or sell on Binance; you only provide "
            "research, risk framing, and exit reminders. "
            f"Keep immutable rules: R${self.paper_cycle_capital:.0f} max reference cycle, "
            f"R${self.paper_trade_amount:.0f} reference per trade, partial take-profit near "
            f"+{self.signal_take_profit_pct:.0f}%, stop-loss at {self.signal_stop_loss_pct:.0f}%, "
            "and no autonomous order submission. "
            f"{instruction}\n\n"
            f"dashboard={_json(dashboard)}\n"
            f"latest_evidence={_json(evidence)}\n"
            f"market_context={_json(market_context)}\n"
            f"trade_plan_reference={_json(trade_plan)}\n"
            f"news_context={_json(news_context)}\n"
            f"manual_positions={_json(manual_positions)}"
        )
        try:
            result = subprocess.run(
                [
                    "hermes",
                    "-z",
                    prompt,
                    "--provider",
                    "gemini",
                    "--model",
                    self.strategy_model,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=180,
            )
            output = result.stdout.strip() or result.stderr.strip() or "Hermes returned no text."
        except FileNotFoundError:
            output = await asyncio.to_thread(self._generate_strategy_review_sync, prompt)
        except Exception as exc:
            output = f"Strategy review failed: {exc}"
        await self.send_long_message("TTM Hermes strategy review", output)

    async def handle_command(self, command: DiscordCommand) -> None:
        text = command.content.strip()
        parts = text.split(maxsplit=1)
        action = parts[1].strip().lower() if len(parts) > 1 else "help"
        if action in {"help", "ajuda"}:
            await self.notifier.send(
                "Comandos TTM: `!ttm status`, `!ttm scan`, `!ttm strategies`, "
                "`!ttm top`, `!ttm dead`, `!ttm family <A|B|D|F>`, `!ttm weekly`, "
                "`!ttm explain <perfil>`, `!ttm propose`, `!ttm killswitch <perfil>`, "
                "`!ttm buy BTCUSDT preco valorBRL`, `!ttm sell BTCUSDT [preco]`, "
                "`!ttm positions`, `!ttm ask <pergunta>`, `!ttm review`, "
                "`!ttm pause`, `!ttm resume`."
            )
        elif action == "status":
            await self.send_status()
        elif action == "scan":
            await self.notifier.send("Hermes recebeu `!ttm scan`. Vou rodar um ciclo paper agora.")
            results = await self.run_strategy_cycle()
            await self.notifier.send(self._strategy_cycle_message(results))
        elif action in {"strategies", "estrategias"}:
            await self.send_strategy_summary()
        elif action == "top":
            await self.send_top_profiles()
        elif action == "dead":
            await self.send_dead_profiles()
        elif action.startswith("family "):
            await self.send_family_metrics(action.split(maxsplit=1)[1])
        elif action.startswith("explain "):
            await self.send_profile_explain(action.split(maxsplit=1)[1].strip())
        elif action == "propose":
            await self.send_proposed_profiles()
        elif action == "weekly":
            await self.send_weekly_report()
        elif action.startswith("killswitch "):
            await self.kill_profile(action.split(maxsplit=1)[1].strip())
        elif self._parse_manual_buy(action) is not None:
            await self.record_manual_buy(action)
        elif self._parse_manual_sell(action) is not None:
            await self.record_manual_sell(action)
        elif action in {"positions", "posicoes", "posicoes abertas"}:
            await self.send_manual_positions()
        elif action.startswith("ask ") or action.startswith("pergunta "):
            question = action.split(maxsplit=1)[1].strip()
            if await self.record_manual_buy(question):
                return
            if await self.record_manual_sell(question):
                return
            await self.notifier.send(
                "Hermes recebeu sua pergunta. Vou responder com base no modo paper e no historico atual."
            )
            await self.notifier.send("Hermes\n" + await self.ask_agent(question))
        elif action == "review":
            await self.notifier.send("Hermes recebeu `!ttm review`. Vou analisar a evidencia paper.")
            await self.send_strategy_review()
        elif action == "pause":
            PAUSE_FILE.write_text("paused by Discord command\n", encoding="utf-8")
            await self.notifier.send("TTM supervisor pausado. Nao abrirei novas posicoes paper.")
        elif action == "resume":
            PAUSE_FILE.unlink(missing_ok=True)
            await self.notifier.send("TTM supervisor retomado em paper-only.")
        else:
            await self.notifier.send("Comando desconhecido. Use `!ttm help`.")

    async def poll_discord_commands(self) -> None:
        if not self.discord_polling_enabled or not self.notifier.can_poll:
            return
        cursor = _load_discord_cursor()
        commands = await self.notifier.fetch_commands(after_message_id=cursor)
        for command in commands:
            task = asyncio.create_task(self._handle_command_safe(command))
            self._command_tasks.add(task)
            task.add_done_callback(self._command_tasks.discard)
        if self.notifier.last_seen_message_id:
            _save_discord_cursor(self.notifier.last_seen_message_id)

    async def _handle_command_safe(self, command: DiscordCommand) -> None:
        try:
            await self.handle_command(command)
        except Exception as exc:
            await self.notifier.send(f"Falha ao executar comando Discord: {_api_error_summary(exc)}")

    async def run_command_loop(self) -> None:
        while True:
            await self.poll_discord_commands()
            await asyncio.sleep(max(self.command_poll_interval_s, 1))

    async def run_trading_loop(self) -> None:
        while True:
            if self.auto_scan_enabled and not PAUSE_FILE.exists():
                try:
                    await self.run_once()
                except Exception as exc:
                    logger.exception("supervisor_scan_failed")
                    await self.notifier.send(f"Falha no scan paper: {_api_error_summary(exc)}")
            await asyncio.sleep(self.scan_interval_s)

    async def run_review_loop(self) -> None:
        await asyncio.sleep(max(self.strategy_review_initial_delay_s, 0))
        while True:
            if not PAUSE_FILE.exists():
                await self.send_strategy_review()
            await asyncio.sleep(self.strategy_review_interval_s)

    async def run_weekly_loop(self) -> None:
        await asyncio.sleep(self.weekly_report_interval_s)
        while True:
            if not PAUSE_FILE.exists():
                await self.send_weekly_report()
            await asyncio.sleep(self.weekly_report_interval_s)

    async def run_once(self) -> None:
        await self.send_binance_signal_board()
        await self.send_exit_signals()
        results = await self.run_strategy_cycle()
        await self.notifier.send(self._strategy_cycle_message(results))

    async def run_forever(self) -> None:
        await self.notifier.send(
            "TTM Hermes supervisor iniciado em paper-only\n"
            f"- capital virtual: R$ {os.getenv('TTM_PAPER_CAPITAL_BRL', '300')}\n"
            f"- teste por trade: R$ {self.paper_trade_amount}\n"
            f"- scan automatico: {self.auto_scan_enabled}; ciclo: {self.scan_interval_s}s\n"
            f"- review automatico: {self.strategy_review_interval_s}s\n"
            f"- modelo analise: {self.strategy_model}\n"
            f"- modelo rapido: {self.fast_model}"
        )
        await asyncio.gather(
            self.run_command_loop(),
            self.run_trading_loop(),
            self.run_review_loop(),
            self.run_weekly_loop(),
        )


async def async_main() -> None:
    parser = argparse.ArgumentParser(description="TTM personal paper supervisor")
    parser.add_argument("--once", action="store_true", help="Run one paper scan and exit")
    parser.add_argument("--status", action="store_true", help="Send one status message and exit")
    parser.add_argument("--review", action="store_true", help="Send one Hermes strategy review and exit")
    args = parser.parse_args()
    supervisor = TtmSupervisor()
    if args.status:
        await supervisor.send_status()
    elif args.review:
        await supervisor.send_strategy_review()
    elif args.once:
        await supervisor.run_once()
    else:
        await supervisor.run_forever()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(async_main())


if __name__ == "__main__":
    main()

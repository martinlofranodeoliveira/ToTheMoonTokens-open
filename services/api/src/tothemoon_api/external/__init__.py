from __future__ import annotations

from tothemoon_api.external.adapters import adapter_contract_payload
from tothemoon_api.external.market import get_market_adapters
from tothemoon_api.external.security import get_security_adapters


def get_external_adapter_contract() -> dict[str, object]:
    return adapter_contract_payload([*get_security_adapters(), *get_market_adapters()])

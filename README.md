# ToTheMoonTokens Open

Open edition containing the complete current API source, migrations, unit tests, React dashboard and optional supervisor source. The current local changes include provider configuration, copilot/admin flows, risk-book logic and outcome persistence. Operational automation, private history, real transaction evidence, screenshots and deployment credentials are excluded. This edition is for local development with synthetic data. No exchange, wallet, payment, settlement, live trading or account connection is required or authorized by these instructions.

The API configuration defaults disable live trading, mainnet, autonomous payments and wallet bootstrap. Keep these switches disabled. Set a fresh local JWT_SECRET through your own environment; no reusable signing secret is shipped. The API requires Python 3.12+ and the dependencies declared in services/api/pyproject.toml. The dashboard source and pinned npm dependencies are in apps/web-next. Never run financial integration scripts as a setup step.

The API and frontend passed the isolated unit/mock checks, typechecking and build described below. No live integration was run. MIT covers the original application code; dependencies keep their upstream licenses and are installed separately. See NOTICE.md.


The optional `bot/` files are distributed as source only. No service/timer, supervisor loop, exchange action, wallet task or Discord notification is enabled by installation or by the API/frontend instructions. Review and configure any optional integration separately before choosing to run it.

The proof-summary route has no bundled historical transactions, wallet inventory or fixed proof date. Its default response is empty/offline; the 63-record fixtures live only in tests and are explicitly synthetic. It does not search an earlier operator's `ops/evidence` directory.


## Local development

Create a Python 3.12+ virtual environment and install the declared API dependencies
from `services/api/pyproject.toml`; development requirements are in its `dev` extra.
Generate your own fresh signing secret and keep it in the process environment or
an ignored `.env`, never in source. Missing/short JWT secrets now produce a generic
503 before token signing or verification, including in local mode.

From `services/api`, after setting `JWT_SECRET` to a fresh value of at least
32 characters, run `uvicorn tothemoon_api.main:app --host 127.0.0.1 --port 8010`
with `src` on `PYTHONPATH`. Leave live/mainnet/autonomous/bootstrap flags disabled.

From `apps/web-next`, install with `npm ci --ignore-scripts` and run `npm run dev`.
The dashboard uses http://127.0.0.1:4173 and proxies its API to local port 8010.
`npm run typecheck` checks types and `npm run build` produces the static bundle.
No provider credentials are needed to inspect the landing page or develop against mocks.

## Validation scope

The release checks passed 269 distinct API/unit cases with synthetic fixtures
or mocks, plus TypeScript checking and a Vite build, in an offline namespace with
read-only source/dependencies and no private home or credentials. The 63 records
in summary tests are generated examples representing no real payment.

Validation did not run exchange/wallet/bot/supervisor/backtest services, cloud
deployment, external model/payment APIs or the excluded operational CI/deployment
scripts. Original test files for excluded operations remain in source; this is
not a claim that the entire original test suite or all external integrations ran.


CI uses Python 3.12 and Node.js 22. Dependency installation is separate from
validation: PyPI wheels only, npm lifecycle scripts disabled. The reviewed CI
command selects exactly 19 test files / 269 cases and asserts the JUnit count,
zero failures/errors/skips. It does not select financial live integrations or
operational deployment tests. Both API tests and frontend checks run through
`scripts/checks-offline.py`, with no network or private credentials; dependencies
and source are read-only and build/database scratch stays under `/tmp`.

Provider numeric fields that are missing or non-finite (NaN, +/-Infinity) fall back to explicit defaults in `security.py` (`_float`/`_risk_score`); unavailable providers yield placeholder/degraded audits. This describes unavailable-data handling only and is not investment advice or a safety guarantee.

## Offline dashboard fixtures (synthetic, stdlib only)

For local UI development and mocked tests, `services/api/src/tothemoon_api/offline_dashboard.py` builds fully synthetic empty/degraded panel snapshots without importing or starting API, database, provider, wallet, payment, exchange, bot, model, HTTP, or configuration code. It uses only the standard library and needs no JWT, database, GPU, provider credentials, or extra installation.

From the repository root:

```sh
PYTHONPATH=services/api/src python -c "from tothemoon_api.offline_dashboard import build_offline_dashboard; print(build_offline_dashboard('empty'))"
PYTHONPATH=services/api/src python -m tothemoon_api.offline_dashboard --scenario empty
PYTHONPATH=services/api/src python -m tothemoon_api.offline_dashboard --scenario degraded
```

The helper exposes `build_offline_dashboard(scenario="empty")` and `main(argv=None)`. Only the exact strings `"empty"` and `"degraded"` are accepted; any other type or value raises `ValueError("INVALID_OFFLINE_DASHBOARD_SCENARIO")`. Each call returns a fresh deterministic JSON-serializable dict with `schema_version`, `synthetic`, `scenario`, `health`, `dashboard`, `usage`, and `collections`, suitable for mocking the public `Health`, `Dashboard`, `Usage`, API-key, discovery, proposal, and audit-event shapes. Example values use `synthetic@example.invalid`, `org_id` 0, plan `"offline-demo"`, zero counters, and empty collections.

Both scenarios are static fixtures, not real health, history, earnings, or production proof. `empty` reports `ok: true` with provider status `"offline"`; `degraded` reports `ok: false` with `"SYNTHETIC_DATA_UNAVAILABLE"`. No existing endpoint, route, login flow, or frontend behavior is changed or automatically wired to these fixtures.


<!-- community-core:offline-quota-guard -->
### Synthetic Offline Quota Guard Evaluator

Helps local developers and test authors exercise synthetic quota decisions for simulate_order, token_audit and active_api_keys without network, credentials, or live services. It gives a single deterministic check for limit, usage and requested counts, reducing mock drift in offline tests. It performs no fetching, storage, or advice and makes no claim about real plans, earnings, or production enforcement.

Reusable deterministic JSON core: `services/api/src/tothemoon_api/community/offline_quota_guard.py`, API `run_feature(payload)`. This module is independently tested; application/UI integration is outside this change.

Exposes run_feature(payload). payload SHOULD be an object; any non-object payload yields {ok:false, allowed:false, action:"unknown", limit:0, used:0, requested:1, remaining:0, issues:["INVALID_PAYLOAD"]}. For object payloads, fields are validated in fixed order: action MUST be exactly one of "simulate_order", "token_audit", "active_api_keys" else INVALID_ACTION; quota MUST be an object else INVALID_QUOTA, otherwise quota[action] MUST be an integer 0..1000000 else INVALID_QUOTA_LIMIT; usage MUST be an object else INVALID_USAGE, otherwise usage[action] MUST be an integer 0..1000000 else INVALID_USAGE_VALUE; requested when missing defaults to 1, otherwise MUST be an integer 1..1000000 else INVALID_REQUESTED. Result action echoes input action when it is one of the three allowed values, otherwise "unknown". Result limit is quota[action] when valid else 0; used is usage[action] when valid else 0; requested is normalized requested else 1 on INVALID_REQUESTED; remaining is max(0, limit-used) when quota and usage values are valid else 0; allowed is true only when issues is empty and used+requested<=limit, otherwise false; issues lists all applicable codes in the fixed order above; ok is true only when issues is empty. The function is total, deterministic and pure with no I/O, environment, time, randomness, callbacks or dependencies and performs no trading, payment, or advice.

Examples (JSON input and expected output):

```json
{
  "input": {
    "action": "simulate_order",
    "quota": {
      "active_api_keys": 2,
      "simulate_order": 5,
      "token_audit": 5
    },
    "requested": 1,
    "usage": {
      "active_api_keys": 0,
      "simulate_order": 2,
      "token_audit": 0
    }
  },
  "output": {
    "action": "simulate_order",
    "allowed": true,
    "issues": [],
    "limit": 5,
    "ok": true,
    "remaining": 3,
    "requested": 1,
    "used": 2
  }
}
```

```json
{
  "input": {
    "action": "token_audit",
    "quota": {
      "active_api_keys": 2,
      "simulate_order": 5,
      "token_audit": 1
    },
    "requested": 1,
    "usage": {
      "active_api_keys": 0,
      "simulate_order": 0,
      "token_audit": 1
    }
  },
  "output": {
    "action": "token_audit",
    "allowed": false,
    "issues": [],
    "limit": 1,
    "ok": true,
    "remaining": 0,
    "requested": 1,
    "used": 1
  }
}
```

```json
{
  "input": {
    "action": "unknown_action",
    "quota": {},
    "usage": {}
  },
  "output": {
    "action": "unknown",
    "allowed": false,
    "issues": [
      "INVALID_ACTION",
      "INVALID_QUOTA_LIMIT",
      "INVALID_USAGE_VALUE"
    ],
    "limit": 0,
    "ok": false,
    "remaining": 0,
    "requested": 1,
    "used": 0
  }
}
```

Public regression tests: `lab_tests/test_community_offline_quota_guard.py`.


<!-- community-core:offline-token-discovery-check -->
### Synthetic Offline Token Discovery Checker for Local Mocks

Helps local developers and test authors exercise synthetic token_discoveries shape decisions from offline dashboard fixtures without network, credentials, or live services. It gives a single deterministic check for symbol, chain, risk_score and flagged with normalized outputs and fixed issue codes, reducing mock drift in offline tests. It performs no fetching, storage, trading, payment, or advice and makes no claim about real tokens, prices, risk, or production enforcement. This module is independently testable; application and UI integration are outside this change.

Reusable deterministic JSON core: `services/api/src/tothemoon_api/community/offline_token_discovery_check.py`, API `run_feature(payload)`. This module is independently tested; application/UI integration is outside this change.

Exposes run_feature(payload). payload SHOULD be a dict; any non-dict payload yields {ok:false, symbol:"", chain:"unknown", risk_score:0, flagged:false, issues:["INVALID_PAYLOAD"]}. For dict payloads extra keys are ignored and fields are validated in fixed order: symbol MUST be a string whose stripped length is 1..20 and whose stripped characters are only A-Z a-z 0-9 dot underscore hyphen else INVALID_SYMBOL; chain when missing defaults to "unknown" without error, otherwise MUST be exactly one of "ethereum", "solana", "base" else INVALID_CHAIN; risk_score when missing defaults to 0 without error, otherwise MUST be an int or float excluding bool that is finite and in range 0..100 inclusive else INVALID_RISK_SCORE; flagged when missing defaults to false without error, otherwise MUST be of type bool else INVALID_FLAGGED. Result symbol is stripped uppercased input when valid else ""; chain echoes input when valid or "unknown" when missing else "unknown"; risk_score echoes input when valid or 0 when missing else 0; flagged is false when missing, echoes input when valid, else false; issues lists all applicable codes among INVALID_SYMBOL, INVALID_CHAIN, INVALID_RISK_SCORE, INVALID_FLAGGED in that fixed order; ok is true only when issues is empty. The function is total, deterministic and pure with no I/O, environment, time, randomness, callbacks or dependencies, preserves input, and performs no fetching, storage, trading, payment, or advice and makes no claim about real tokens, risk, or production enforcement. This module is independently testable; application and UI integration are outside this change.

Examples (JSON input and expected output):

```json
{
  "input": {
    "chain": "ethereum",
    "flagged": false,
    "risk_score": 12.5,
    "symbol": " weth "
  },
  "output": {
    "chain": "ethereum",
    "flagged": false,
    "issues": [],
    "ok": true,
    "risk_score": 12.5,
    "symbol": "WETH"
  }
}
```

```json
{
  "input": {
    "symbol": "SOL"
  },
  "output": {
    "chain": "unknown",
    "flagged": false,
    "issues": [],
    "ok": true,
    "risk_score": 0,
    "symbol": "SOL"
  }
}
```

```json
{
  "input": {
    "chain": "unknown-chain",
    "flagged": 1,
    "risk_score": 999,
    "symbol": ""
  },
  "output": {
    "chain": "unknown",
    "flagged": false,
    "issues": [
      "INVALID_SYMBOL",
      "INVALID_CHAIN",
      "INVALID_RISK_SCORE",
      "INVALID_FLAGGED"
    ],
    "ok": false,
    "risk_score": 0,
    "symbol": ""
  }
}
```

Public regression tests: `lab_tests/test_community_offline_token_discovery_check.py`.


<!-- community-core:offline-copilot-proposal-check -->
### Synthetic Offline Copilot Proposal Checker for Local Mocks

Helps local developers and test authors exercise synthetic copilot_proposals shape decisions from offline dashboard fixtures without network, credentials, or live services. It gives a single deterministic check for title, strategy, confidence and status with normalized outputs and fixed issue codes, reducing mock drift in offline tests. It performs no fetching, storage, trading, payment, or advice and makes no claim about real strategies, performance, or production enforcement. This module is independently testable; application and UI integration are outside this change.

Reusable deterministic JSON core: `services/api/src/tothemoon_api/community/offline_copilot_proposal_check.py`, API `run_feature(payload)`. This module is independently tested; application/UI integration is outside this change.

Exposes run_feature(payload). payload SHOULD be a dict; any non-dict payload yields {ok:false, title:"", strategy:"unknown", confidence:0, status:"draft", issues:["INVALID_PAYLOAD"]}. For dict payloads extra keys are ignored and fields are validated in fixed order: title MUST be a string whose stripped length is 1..80 and whose stripped characters are only A-Z a-z 0-9 space underscore hyphen dot else INVALID_TITLE; strategy when missing defaults to "unknown" without error, otherwise MUST be exactly one of "momentum", "mean-reversion", "breakout" else INVALID_STRATEGY; confidence when missing defaults to 0 without error, otherwise MUST be an int or float excluding bool that is finite and in range 0..100 inclusive else INVALID_CONFIDENCE; status when missing defaults to "draft" without error, otherwise MUST be exactly one of "draft", "proposed", "approved", "rejected" else INVALID_STATUS. Result title is stripped input when valid else ""; strategy echoes input when valid or "unknown" when missing else "unknown"; confidence echoes input when valid or 0 when missing else 0; status is "draft" when missing, echoes input when valid, else "draft"; issues lists all applicable codes among INVALID_TITLE, INVALID_STRATEGY, INVALID_CONFIDENCE, INVALID_STATUS in that fixed order; ok is true only when issues is empty. The pattern mirrors synthetic copilot_proposals shapes from offline dashboard fixtures for local offline development with synthetic data. The function is total, deterministic and pure with no I/O, environment, time, randomness, callbacks or dependencies, preserves input, and performs no fetching, storage, trading, payment, or advice and makes no claim about real strategies, performance, or production enforcement. This module is independently testable; application and UI integration are outside this change.

Examples (JSON input and expected output):

```json
{
  "input": {
    "confidence": 75.5,
    "status": "proposed",
    "strategy": "momentum",
    "title": " Momentum scan "
  },
  "output": {
    "confidence": 75.5,
    "issues": [],
    "ok": true,
    "status": "proposed",
    "strategy": "momentum",
    "title": "Momentum scan"
  }
}
```

```json
{
  "input": {
    "title": "SOL breakout"
  },
  "output": {
    "confidence": 0,
    "issues": [],
    "ok": true,
    "status": "draft",
    "strategy": "unknown",
    "title": "SOL breakout"
  }
}
```

```json
{
  "input": {
    "confidence": 999,
    "status": "live",
    "strategy": "scalping",
    "title": ""
  },
  "output": {
    "confidence": 0,
    "issues": [
      "INVALID_TITLE",
      "INVALID_STRATEGY",
      "INVALID_CONFIDENCE",
      "INVALID_STATUS"
    ],
    "ok": false,
    "status": "draft",
    "strategy": "unknown",
    "title": ""
  }
}
```

Public regression tests: `lab_tests/test_community_offline_copilot_proposal_check.py`.


<!-- community-core:offline-health-check -->
### Synthetic Offline Health Checker for Local Mocks

Helps local developers and test authors exercise synthetic health shape decisions from offline dashboard fixtures without network, credentials, or live services. It gives a single deterministic check for healthy, mode and provider_status with normalized outputs and fixed issue codes, reducing mock drift in offline tests. It performs no fetching, storage, trading, payment, or advice and makes no claim about real health, availability, or production enforcement. This module is independently testable; application integration is outside this change.

Reusable deterministic JSON core: `services/api/src/tothemoon_api/community/offline_health_check.py`, API `run_feature(payload)`. This module is independently tested; application/UI integration is outside this change.

Exposes run_feature(payload). payload SHOULD be a dict; any non-dict payload yields {ok:false, healthy:false, mode:"synthetic", provider_status:"offline", issues:["INVALID_PAYLOAD"]}. For dict payloads extra keys are ignored and fields are validated in fixed order: healthy when missing defaults to true without error, otherwise MUST be exactly type bool else INVALID_HEALTHY; mode when missing defaults to "synthetic" without error, otherwise MUST be exactly "synthetic" else INVALID_MODE; provider_status when missing defaults to "offline" without error, otherwise MUST be exactly one of "offline", "degraded" else INVALID_PROVIDER_STATUS. Result healthy is true when missing, echoes input when valid, else false; mode is "synthetic" when missing, echoes input when valid, else "synthetic"; provider_status is "offline" when missing, echoes input when valid, else "offline"; issues lists all applicable codes among INVALID_HEALTHY, INVALID_MODE, INVALID_PROVIDER_STATUS in that fixed order; ok is true only when issues is empty. The function is total, deterministic and pure with no I/O, environment, time, randomness, callbacks or dependencies, preserves input, and performs no fetching, storage, trading, payment, or advice and makes no claim about real health, availability, or production enforcement. This module is independently testable; application integration is outside this change.

Examples (JSON input and expected output):

```json
{
  "input": {
    "healthy": true,
    "mode": "synthetic",
    "provider_status": "offline"
  },
  "output": {
    "healthy": true,
    "issues": [],
    "mode": "synthetic",
    "ok": true,
    "provider_status": "offline"
  }
}
```

```json
{
  "input": {
    "healthy": false,
    "provider_status": "degraded"
  },
  "output": {
    "healthy": false,
    "issues": [],
    "mode": "synthetic",
    "ok": true,
    "provider_status": "degraded"
  }
}
```

```json
{
  "input": {
    "healthy": 1,
    "mode": "live",
    "provider_status": "online"
  },
  "output": {
    "healthy": false,
    "issues": [
      "INVALID_HEALTHY",
      "INVALID_MODE",
      "INVALID_PROVIDER_STATUS"
    ],
    "mode": "synthetic",
    "ok": false,
    "provider_status": "offline"
  }
}
```

Public regression tests: `lab_tests/test_community_offline_health_check.py`.


<!-- community-core:offline-dashboard-check -->
### Synthetic Offline Dashboard Checker for Local Mocks

Helps local developers and test authors exercise synthetic dashboard shape decisions from offline dashboard fixtures without network, credentials, or live services. It gives a single deterministic check for email, org_id and requests_this_month with normalized outputs and fixed issue codes, reducing mock drift in offline tests. It performs no fetching, storage, trading, payment, or advice and makes no claim about real accounts, activity, or production enforcement. This module is independently testable; application and UI integration are outside this change.

Reusable deterministic JSON core: `services/api/src/tothemoon_api/community/offline_dashboard_check.py`, API `run_feature(payload)`. This module is independently tested; application/UI integration is outside this change.

Exposes run_feature(payload). payload SHOULD be a dict; any non-dict payload yields {ok:false, email:"", org_id:0, requests_this_month:0, issues:["INVALID_PAYLOAD"]}. For dict payloads extra keys are ignored and fields are validated in fixed order: email when missing defaults to "synthetic@example.invalid" without error, otherwise MUST be a string whose stripped length is 5..80 and whose stripped value contains exactly one "@" with "." in the part after "@" else INVALID_EMAIL; org_id when missing defaults to 0 without error, otherwise MUST be an integer excluding bool in range 0..1000000 inclusive else INVALID_ORG_ID; requests_this_month when missing defaults to 0 without error, otherwise MUST be an integer excluding bool in range 0..1000000 inclusive else INVALID_REQUESTS_THIS_MONTH. Result email is "synthetic@example.invalid" when missing, stripped input when valid, else ""; org_id is 0 when missing, echoes input when valid, else 0; requests_this_month is 0 when missing, echoes input when valid, else 0; issues lists all applicable codes among INVALID_EMAIL, INVALID_ORG_ID, INVALID_REQUESTS_THIS_MONTH in that fixed order; ok is true only when issues is empty. The function is total, deterministic and pure with no I/O, environment, time, randomness, callbacks or dependencies, preserves input, and performs no fetching, storage, trading, payment, or advice and makes no claim about real accounts, activity, or production enforcement. This module is independently testable; application and UI integration are outside this change.

Examples (JSON input and expected output):

```json
{
  "input": {
    "email": "synthetic@example.invalid",
    "org_id": 0,
    "requests_this_month": 0
  },
  "output": {
    "email": "synthetic@example.invalid",
    "issues": [],
    "ok": true,
    "org_id": 0,
    "requests_this_month": 0
  }
}
```

```json
{
  "input": {
    "org_id": 5
  },
  "output": {
    "email": "synthetic@example.invalid",
    "issues": [],
    "ok": true,
    "org_id": 5,
    "requests_this_month": 0
  }
}
```

```json
{
  "input": {
    "email": "bad",
    "org_id": -1,
    "requests_this_month": "many"
  },
  "output": {
    "email": "",
    "issues": [
      "INVALID_EMAIL",
      "INVALID_ORG_ID",
      "INVALID_REQUESTS_THIS_MONTH"
    ],
    "ok": false,
    "org_id": 0,
    "requests_this_month": 0
  }
}
```

Public regression tests: `lab_tests/test_community_offline_dashboard_check.py`.


<!-- community-core:offline-api-key-check -->
### Synthetic Offline API Key Checker for Local Mocks

Helps local developers and test authors exercise synthetic api_keys shape decisions from offline dashboard fixtures without network, credentials, or live services. It gives a single deterministic check for label, scope and active with normalized outputs and fixed issue codes, reducing mock drift in offline tests. It performs no fetching, storage, trading, payment, or advice and makes no claim about real credentials, permissions, or production enforcement. This module is independently testable; application and UI integration are outside this change.

Reusable deterministic JSON core: `services/api/src/tothemoon_api/community/offline_api_key_check.py`, API `run_feature(payload)`. This module is independently tested; application/UI integration is outside this change.

Exposes run_feature(payload). payload SHOULD be a dict; any non-dict payload yields {ok:false, label:"", scope:"read", active:false, issues:["INVALID_PAYLOAD"]}. For dict payloads extra keys are ignored and fields are validated in fixed order: label MUST be a string whose stripped length is 1..30 and whose stripped characters are only A-Z a-z 0-9 space underscore hyphen else INVALID_LABEL; scope when missing defaults to "read" without error, otherwise MUST be exactly one of "read", "write", "audit" else INVALID_SCOPE; active when missing defaults to false without error, otherwise MUST be of type bool else INVALID_ACTIVE. Result label is stripped input when valid else ""; scope is "read" when missing, echoes input when valid, else "read"; active is false when missing, echoes input when valid, else false; issues lists all applicable codes among INVALID_LABEL, INVALID_SCOPE, INVALID_ACTIVE in that fixed order; ok is true only when issues is empty. The function is total, deterministic and pure with no I/O, environment, time, randomness, callbacks or dependencies, preserves input, and performs no fetching, storage, trading, payment, or advice and makes no claim about real credentials, permissions, or production enforcement. This module is independently testable; application and UI integration are outside this change.

Examples (JSON input and expected output):

```json
{
  "input": {
    "active": true,
    "label": " dev-key_1 ",
    "scope": "write"
  },
  "output": {
    "active": true,
    "issues": [],
    "label": "dev-key_1",
    "ok": true,
    "scope": "write"
  }
}
```

```json
{
  "input": {
    "label": "a"
  },
  "output": {
    "active": false,
    "issues": [],
    "label": "a",
    "ok": true,
    "scope": "read"
  }
}
```

```json
{
  "input": {
    "active": 1,
    "label": "",
    "scope": "super"
  },
  "output": {
    "active": false,
    "issues": [
      "INVALID_LABEL",
      "INVALID_SCOPE",
      "INVALID_ACTIVE"
    ],
    "label": "",
    "ok": false,
    "scope": "read"
  }
}
```

Public regression tests: `lab_tests/test_community_offline_api_key_check.py`.

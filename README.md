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

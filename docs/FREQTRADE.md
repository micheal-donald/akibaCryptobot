# Freqtrade paper deployment

The replacement production path is Freqtrade 2026.9, pinned to the official
Docker manifest digest in `deploy/freqtrade/Dockerfile`. The former custom
engine remains in the repository for reference; it is not part of this image.
The GitHub CI image adds only the paper configuration, strategy and public
portfolio adapter to the upstream runtime. CI builds to GHCR and Dokploy deploys
an immutable digest using `scripts/freqtrade_deploy.py`.

## Components and data flow

Binance public market data → Freqtrade strategy/risk/execution simulator →
Freqtrade SQLite trade database → private authenticated REST API → bounded,
15-second in-memory public projection → `cryptobot.akibasoftware.com`.

- `engine` / `akiba-freqtrade`: owns all trading state and simulated orders.
- `bot` / `akiba-portfolio`: read-only web service, retains no database and cannot
  route user requests to arbitrary Freqtrade endpoints. The Dokploy domain
  continues to target service `bot`, port 8000.
- FreqUI and its control API bind to host **127.0.0.1:8080**. Only the portfolio
  service joins the Dokploy proxy network. Host port 8000 is also localhost-only.
- No Binance credentials, Telegram credentials, old bot environment, FreqAI
  models, or ML training jobs are used by this deployment.

## Experiment configuration and limits

Configuration: `deploy/freqtrade/config.json` (not legacy `config/config.json`).

| Setting | Value |
|---|---|
| Mode | `dry_run=true`, Spot; no exchange orders |
| Initial virtual wallet | 1,000 USDT |
| Pairs | SOL/USDT, BTC/USDT, ETH/USDT (static order) |
| Timeframe | 1 hour; completed candles only |
| Stake | 50 USDT per position; maximum 2 concurrent |
| Fee assumption | 0.1% each side |
| Stop | 3% of entry price, simulated; actual loss can exceed this with gaps/fees |
| ROI exit | 1%, plus momentum reversal exits |
| Re-entry cooldown | 3 candles per pair |
| Stoploss guard | 2 qualifying losing stops in 24 candles block entries globally for 6 candles |

`AkibaADXPaper` independently expresses the entry/exit conditions of the public
[ADXMomentum reference](https://github.com/freqtrade/freqtrade-strategies/blob/main/user_data/strategies/berlinguyinca/ADXMomentum.py):
ADX(14)>25, MOM(14)>0, PLUS_DI(25)>25 and above MINUS_DI(25) for entry; opposite
momentum/DI conditions for exit. It adds positive volume, 200 warmup bars,
cooldown/loss protection, and tightens the original 25% stop to 3%. It has no
trailing stop, averaging down, shorts, leverage or forced entries. These changes
make it a **separate unvalidated strategy**, not the original strategy's results.
The strategy rejects live mode. The startup wrapper also validates wallet/stake
limits, refuses environment overrides and accepts only API authentication in
the private configuration file. Stops here are simulated, not native Binance OCO.

This is an execution and forward-testing experiment, not a promise of profit.
The user's $50/day aspiration is not an entry trigger or risk-sizing input.
Historical test periods already inspected in prior research are exploratory.
Any native backtest must use `--enable-protections`; fees are applied both sides.
Higher-fee runs are cost sensitivity, not a full model of slippage/order books.

## Access

Public portfolio: https://cryptobot.akibasoftware.com — no login, read-only.

Private FreqUI:

```bash
ssh -N -i ~/.ssh/akibabot_vultr -L 8080:127.0.0.1:8080 root@66.42.32.233
# Open http://localhost:8080
```

Username: `akiba`. Retrieve the generated password privately over SSH:

```bash
ssh -i ~/.ssh/akibabot_vultr root@66.42.32.233 \
  "python3 -c \"import json; print(json.load(open('/etc/akibabot/freqtrade-api.json'))['api_server']['password'])\""
```

Do not share this password or expose port 8080 publicly. The API credentials
are generated on-server, stored with mode 0600, mounted read-only, and never
sent to the public browser. Dokploy access remains the existing SSH tunnel on
port 3000. Telegram is disabled; operate through private FreqUI for now.

## Public API

Only `GET /`, `/portfolio.js`, `/portfolio.css`, `/api/health`, `/api/status`,
and `/api/portfolio` are implemented. No legacy control routes or public FreqUI.
All HTTP writes return 403. Unknown paths return 404. No generic API proxy,
WebSocket pass-through, credentials, logs or raw configuration are exposed.

`/api/portfolio` and `/api/status` return an explicit field allowlist: engine,
version, experiment settings, heartbeat age, realized/total estimated PnL,
open positions, recent completed trades, and active entry locks. Requests share
a 15-second cache including failures. No historical snapshots are persisted.
`/api/health` returns 200 only for a running dry-run engine with a heartbeat
less than 90 seconds old and successful upstream reads; otherwise 503. This
checks loop freshness, not independent freshness of every exchange data feed.
The browser clears figures on unavailable/stale data rather than displaying
old results as current. Virtual equity is 1,000 plus Freqtrade's total net PnL;
hosting, taxes and differences from real fills are excluded.

## Storage, migration and rollback

Only `/opt/akibabot/freqtrade/user_data` persists for the new engine. Trade and
order history lives in `tradesv3.dryrun.sqlite`. Runtime OHLCV is not collected
into a custom telemetry database. Docker logs rotate at 2 × 5 MB per service.
Research downloads and exports remain separate, under ignored `results/freqtrade`
locally. Keep trade history; do not trim open trades or reuse the legacy DB.

First cutover confirms the old service reports paper mode, stops it, disables
its restart policy, and copies its data directory into a private timestamped
`/etc/akibabot/before-freqtrade-*` archive. The complete old Dokploy compose,
environment and state are backed up there. The old systemd service remains
disabled. Binance Testnet holdings/orders are not imported into simulated
Freqtrade positions; the virtual experiment starts independently.

Every deployment uses the existing `/run/akiba-deploy.lock`, saves the previous
compose/environment, and checks **both** container image digests plus dry-run
mode, strategy, pairs and heartbeat. Failure stops the replacement before
requesting restoration of the prior compose/environment. Inspect rollback
health before considering recovery complete. No database is deleted.

Legacy model training is retired; its workflow is manual-only and performs no
training, server changes, restarts or messages. Avoid manually running legacy
deploy scripts. No second trading engine should be started against this run.

## Verification and native backtest

```bash
docker build -f deploy/freqtrade/Dockerfile -t akiba-test .
docker run --rm --entrypoint python -w /app \
  -v "$PWD/tests/test_freqtrade_migration.py:/tmp/test_migration.py:ro" \
  akiba-test /tmp/test_migration.py -v
```

Use a separate user directory for research. The deployed image's strategy path
is `/app/deploy/freqtrade/strategies`; all backtest/export files should be under
that research directory. Do not place research history in the production DB.
Consult the official [backtesting](https://www.freqtrade.io/en/stable/backtesting/)
and [lookahead analysis](https://www.freqtrade.io/en/stable/lookahead-analysis/)
documentation. A running engine and a passing causality test establish neither
profitability nor live exchange execution reliability.

### Migration benchmark (10 October 2026)

Native Freqtrade 2026.9, 1 July–10 October 2026, $1,000 virtual capital,
$50 stakes, two positions, all strategy protections enabled:

| Costs each side | Trades | Net PnL | Return | Profit factor | Closed-trade drawdown |
|---|---:|---:|---:|---:|---:|
| 0.1% | 122 | −2.80 USDT | −0.28% | 0.94 | 1.39% |
| 0.3% | 104 | −26.83 USDT | −2.68% | 0.56 | 3.17% |

The strategy fails profitability checks and is deployed **only as an operational
paper baseline**. No parameters were optimized after these results. Native
lookahead analysis checked 30 signals and found no bias; this is a sampled check,
not a guarantee. Raw candles cover June onward for warmup. Data hashes and
metrics are in [the migration results](research/2026-10-10-freqtrade-migration-results.json).
Simulated stop/ROI ordering and fills follow Freqtrade's native assumptions,
not the earlier custom OCO simulator. Neither run includes hosting costs.

Reproduce using a separate writable research directory mounted at `/research`,
with a copy of the paper config disabling `api_server.enabled`:

```bash
# Replace COMMAND below with download-data, backtesting or lookahead-analysis.
docker run --rm --entrypoint freqtrade \
  -v "$PWD/results/freqtrade:/research" akiba-test COMMAND \
  --config /research/research.json --userdir /research/user_data
# Download: --timeframes 1h --timerange 20260601-20261010
# Backtest: --strategy-path /app/deploy/freqtrade/strategies \
#   --timerange 20260701-20261010 --enable-protections --cache none --export trades
# Cost stress: same backtest plus --fee 0.003
# Lookahead: --strategy-path /app/deploy/freqtrade/strategies \
#   --timerange 20260701-20261010 --minimum-trade-amount 10 --targeted-trade-amount 30
```

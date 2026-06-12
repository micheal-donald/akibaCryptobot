# Operator Dashboard Enhancements Spec

## Status

Draft implementation spec for an operator-facing enhancement track, informed by a gap analysis against [FreqUI](https://www.freqtrade.io/en/stable/freq-ui/). Not tied to a runtime phase — the runtime modes (paper_runtime_primary, live canary ladder) are decided by the Phase 6 / 8 / 11 / 19 / 20 specs, which this track does not touch.

## Purpose

The current dashboard (`src/ui/index.html`) is a read-heavy operator view with one write surface: `/trading/pause` and `/trading/resume`. Live-promotion controls (`/trading/live/approve`, `/trading/live/rollback`) already require an `X-Operator-Token` header via `src/api/operator_auth.py`, but the dashboard itself is unauthenticated and has no UI that sends that header.

FreqUI ships with: JWT auth, force entry/exit per position, in-browser backtest runner, pair-lock management, plot configurator, candle styling, theme toggle, and favicon trade counters. Not all of that is worth copying — some of it fights our architecture (e.g. FreqUI's plot configurator assumes a single strategy-owned dataframe). This spec selects the subset that is both high-value for paper/live operator workflows and cheap to add without rewriting existing surfaces.

## Dependencies

- [src/api/operator_auth.py](/home/lekim/Projects/akibaCryptobot/src/api/operator_auth.py)
- [src/api/routes/trading.py](/home/lekim/Projects/akibaCryptobot/src/api/routes/trading.py)
- [src/trading/position_manager.py](/home/lekim/Projects/akibaCryptobot/src/trading/position_manager.py)
- [src/backtesting/engine.py](/home/lekim/Projects/akibaCryptobot/src/backtesting/engine.py)
- [src/bot.py](/home/lekim/Projects/akibaCryptobot/src/bot.py)
- [src/ui/index.html](/home/lekim/Projects/akibaCryptobot/src/ui/index.html)
- [src/strategies/base_strategy.py](/home/lekim/Projects/akibaCryptobot/src/strategies/base_strategy.py)
- [config/config.json](/home/lekim/Projects/akibaCryptobot/config/config.json)

## Problem Statement

1. **Unauthenticated UI surface** — The static dashboard at `/` (and its `/api/*` consumers) does not prompt for or attach an operator token. Adding new mutating endpoints (force-exit, circuit reset, backtest trigger) is risky until the UI has a real auth seam.
2. **No per-position force-exit** — `PositionManager.close_position(symbol, reason)` exists and is wired for stop-loss / take-profit / reconciliation, but there is no HTTP or UI entry point for an operator to close a single position manually. The only operator control is global pause/resume.
3. **Backtesting is CLI-only** — `BacktestEngine` (`src/backtesting/engine.py:54`) is async-ready and returns a structured `BacktestResult`, but the bot process itself does not expose it. Operators must run `make` targets on a dev box. FreqUI's in-app backtest is a primary reason teams stay in it.
4. **Circuit-breaker reset is implicit** — `AkibaCryptoBot.circuit_breaker_active` (`src/bot.py:128`) auto-clears when the risk check that tripped it clears. There is no way for an operator to force-clear a stale trip (e.g. a volatility spike that is now stale but the check window still sees it), nor any audit record of who cleared it.
5. **Favicon / tab presence is inert** — When an operator has five browser tabs open, the dashboard tab is indistinguishable. FreqUI's favicon changes to reflect open-trade count, which makes the tab scannable at a glance.

## Goals

1. Establish a single auth story for the dashboard that reuses the existing `X-Operator-Token` contract so mutating UI controls can be added without a second auth system.
2. Add one mutating operator control per cycle (force-exit first, then circuit reset) so we can measure the blast radius of each before stacking more.
3. Expose the existing `BacktestEngine` through a read/write API pair (`POST` to start, `GET` to poll) so the heavy work stays in-process and the UI stays thin.
4. Keep the UI a single static page (`src/ui/index.html`) — no framework, no build step, no bundler. Additions are vanilla JS fetched from existing `/api/*` endpoints.
5. Do not change trading-path ownership. Every control in this spec routes through existing service seams (PositionManager, BacktestEngine, bot state), not through the runtime event bus.

## Non-Goals

1. Replacing the dashboard with a SPA framework (React/Vue/Svelte). FreqUI's complexity is partly justified by its multi-bot / multi-user model; we are a single-tenant operator surface.
2. Porting FreqUI's plot configurator, candlestick chart, theme toggle, pair-lock management UI, pairlist-testing UI, timezone selector, data-download UI, or in-app notification center. Each is either low-value for our workflow, duplicates Telegram, or assumes a different strategy contract than ours.
3. Adding multi-user accounts, roles, or per-user audit scoping. Operator token is a shared capability token; that is the model and it stays.
4. Changing how the runtime actually decides entries/exits. Force-exit must reuse `PositionManager.close_position`; it must not bypass reconciliation.
5. Shipping a separate live-trading approval UI. Phase 19 already owns `/trading/live/approve` and `/trading/live/rollback`; those endpoints stay CLI/ops until Phase 19 revisits operator UX.

## Architecture Decision

Treat the dashboard as an **operator console layered over existing service seams**, not as a new write path.

```
Browser (index.html)
     │ fetch() with X-Operator-Token header
     ▼
FastAPI /api/* routes
     │ Depends(require_operator_token) on mutating routes
     ▼
Existing service seam (PositionManager, BacktestEngine, bot.circuit_breaker_*)
     │
     ▼
Existing runtime (unchanged)
```

Every new UI control must:

1. Send the operator token via the `X-Operator-Token` header.
2. Land on a FastAPI route that declares `dependencies=[Depends(require_operator_token)]`.
3. Call an existing service method whose behavior under test is already known.

This is the same pattern `src/api/routes/trading.py` already uses for `/trading/live/approve` and `/trading/live/rollback` (`src/api/routes/trading.py:113-160`).

## Sequencing Rationale

Ship in this order, one PR per initiative:

1. **Operator token wiring in the UI (Initiative A)** — unblocks 2/3/4. Zero new backend surface; just extends the UI to carry a token.
2. **Force-exit per position (Initiative B)** — highest-value and smallest blast radius of the mutating controls. Reuses `close_position` exactly as stop-loss does.
3. **Circuit-breaker reset (Initiative C)** — second-smallest. One boolean flip on the bot.
4. **Backtest UI (Initiative D)** — largest and most orthogonal. Best shipped after A/B/C so the auth/UX patterns are already proven.
5. **Favicon trade counter (Initiative E)** — pure frontend, no auth, can ship any time but sits last because it has no functional dependency on the others and low urgency.

## Initiative A — UI Operator Token Wiring

### Problem

The dashboard sends no auth header. The only existing mutating endpoints (`/trading/live/approve`, `/trading/live/rollback`) can only be exercised via curl. Adding more mutating endpoints before the UI can authenticate multiplies the number of curl-only controls.

### Scope

- Add a token input + "Save token" control to `src/ui/index.html`, persisted in `sessionStorage` (not `localStorage` — session-scoped limits exposure).
- Introduce a shared `fetchAuthenticated(path, init)` wrapper in the dashboard JS that attaches `X-Operator-Token` when present.
- Add a banner that surfaces 401/403 responses with a "re-enter token" action.
- Document the operator-token lifecycle in `docs/API_REFERENCE.md` and `docs/DEPLOYMENT.md`.

### Non-Goals

- No login page, no cookie, no JWT. The shared-secret model is intentional for a single-tenant operator console.
- No backend change. `AKIBA_OPERATOR_TOKEN` is already the source of truth; this initiative only teaches the UI to send it.

### Current Anchors

- [src/api/operator_auth.py](/home/lekim/Projects/akibaCryptobot/src/api/operator_auth.py) — `require_operator_token` dependency
- [src/api/routes/trading.py:113-160](/home/lekim/Projects/akibaCryptobot/src/api/routes/trading.py) — existing protected endpoints
- [src/ui/index.html](/home/lekim/Projects/akibaCryptobot/src/ui/index.html) — dashboard markup

### Target Files

- `src/ui/index.html` — add token control + `fetchAuthenticated` helper
- `docs/API_REFERENCE.md` — document token header + 401/403 semantics
- `docs/DEPLOYMENT.md` — document `AKIBA_OPERATOR_TOKEN` env var rollout

### Data Contracts

- Header name: `X-Operator-Token` (fixed; matches `operator_auth.py`).
- Storage key: `akiba.operator_token` in `sessionStorage`.
- All mutating `/api/*` calls go through `fetchAuthenticated`. Read-only calls (existing `/api/status`, `/api/metrics`, etc.) continue to use plain `fetch`.

### Implementation Notes

- Token control lives in the config bar (top-right), collapsed by default, expanded when a mutating action 403s.
- `fetchAuthenticated` must never log the token value (not even in `console.error`). Errors surface the HTTP status, not the header contents.
- Clearing the token must clear `sessionStorage` and drop any in-memory copy.
- If the token env var is unset on the backend, `require_operator_token` raises 503. The UI must show "operator controls disabled on this environment" rather than a generic error.

### Failure Modes

- Token not set on backend (503) → UI shows disabled controls + hint pointing at `docs/DEPLOYMENT.md`.
- Token set but wrong (403) → UI shows "token rejected", re-prompts.
- Token set correctly but endpoint still fails (500) → UI surfaces the backend error detail unchanged.

### Observability

- Add a structured log in `operator_auth.py` on rejection (status 403) with the client IP and the path, but never the submitted token value. Already partially present — verify and keep.
- Add a `operator_auth_rejected_total` counter on the telemetry service, keyed by route.

### Tests

- Unit: `fetchAuthenticated` attaches header when token present; attaches no header when token absent; swallows 401/403 into a structured result.
- Integration: `pytest` — hit an existing protected route (`/trading/live/approve` in dry mode) with/without header and verify 403/200 matrix.
- Manual: load dashboard without token → pause/resume still works; set token → force-exit-shaped endpoint (Initiative B, gated) becomes available.

### Rollback Plan

- Pure frontend + docs change. Revert the single UI commit; all existing endpoints continue to work because the header is optional for unprotected endpoints.

### Definition of Done

- Dashboard stores and attaches the operator token for mutating calls.
- Docs updated; `make check` passes.
- No token value appears in server logs or browser console.

## Initiative B — Force-Exit per Position

### Problem

Operators can pause the bot globally but cannot close a single leaking position. During a live canary stage (Phase 11/20), being able to close one position without halting the whole bot is a standard ops requirement.

### Scope

- Add `POST /api/positions/{symbol}/close` with body `{ "reason": str }`, protected by `require_operator_token`.
- Wire the route to `PositionManager.close_position(symbol, reason)`.
- Add a "Close position" button per row in the positions table in `src/ui/index.html`, guarded behind operator-token presence.
- Add an audit record to persistence so a closed-by-operator exit is distinguishable from stop-loss / TP / reconciliation.

### Non-Goals

- No partial closes. Quantity semantics stay at 100% of open quantity.
- No cross-strategy aggregation; force-exit is per `(symbol, position)` tuple.
- No change to how position close is reconciled with the exchange — `close_position` already handles this.

### Current Anchors

- [src/trading/position_manager.py:119](/home/lekim/Projects/akibaCryptobot/src/trading/position_manager.py) — `async def close_position(self, symbol: str, reason: str)`
- [src/api/routes/trading.py](/home/lekim/Projects/akibaCryptobot/src/api/routes/trading.py) — existing POST routes pattern
- [src/bot.py](/home/lekim/Projects/akibaCryptobot/src/bot.py) — `PositionManager` instantiation, position state lookup

### Target Files

- `src/api/routes/trading.py` — new route
- `src/ui/index.html` — per-row button + confirmation modal
- `src/trading/position_manager.py` — extend `close_position` to accept an `operator_initiated: bool` marker if not already present; stamp the exit reason prefix (`operator:<reason>`) consistently
- `tests/api/test_trading_routes.py` (new or existing) — route-level tests
- `docs/API_REFERENCE.md` — document the new route

### Data Contracts

Request:

```json
{ "reason": "manual review — leaking into 1h close" }
```

Response (200):

```json
{
  "symbol": "BTCUSDT",
  "status": "closed",
  "exit_reason": "operator:manual review — leaking into 1h close",
  "closed_at": "2026-04-16T12:00:00+00:00"
}
```

Error responses:
- 400 if `reason` missing or > 256 chars.
- 404 if symbol has no open position.
- 403 if operator token invalid (via dependency).
- 409 if close already in flight for this symbol.

### Implementation Notes

- Debounce: track a per-symbol "close in flight" flag (simple `asyncio.Lock` per symbol on the route module) so double-clicks in the UI don't fire twice. On 409, surface "close already in progress".
- The `reason` field is always prefixed with `operator:` on storage so downstream reporting (`trades.db`) can distinguish operator closes from algorithmic ones.
- The UI button must require a confirmation modal — a one-click close on a live account is too easy.
- If the position's reconciliation state says "remote already closed", the endpoint still returns 200 idempotently with `status: "already_closed"` — do not 409 in that case.

### Failure Modes

- Exchange rejects the close order → return 502 with exchange error; bot state is untouched (reconciler will retry).
- Position closed by stop-loss between UI render and button click → 404 (stale).
- Circuit breaker active → **still allow** operator close (circuit breaker blocks new entries; it must not trap existing positions).

### Observability

- Log: `position_force_exit.requested` → `position_force_exit.submitted` → `position_force_exit.completed` / `.failed` with `symbol`, `reason`, `duration_ms`.
- Telegram: send notification on every successful operator close (reuse existing trade-exit notification path, with a distinct emoji or prefix).
- Metric: `operator_force_exit_total{outcome=completed|failed|already_closed}`.

### Tests

- Unit: route returns 404 when no position; 200 when position closes cleanly; 409 when lock is held.
- Integration: with a sandbox position open, hit the route and assert `PositionManager.close_position` is called once with the expected reason.
- Regression: circuit-breaker-active path still permits close.

### Rollback Plan

- Gate the new button behind a runtime config flag `ui.operator_force_exit_enabled` (default true). To disable in the field, flip to false and redeploy; endpoint still exists but UI does not surface it.
- If the route itself proves unsafe, remove the `dependencies` line (soft-disable via 404) and follow up with a revert.

### Definition of Done

- Route works against a paper account end-to-end.
- UI confirmation modal in place; button hidden when no operator token stored.
- Telegram notification fires on operator close.
- Tests green; docs updated.

## Initiative C — Circuit-Breaker Reset

### Problem

`AkibaCryptoBot.circuit_breaker_active` can stay true longer than the operator wants — e.g. after a volatility spike that has since calmed but still sits inside the rolling window the check uses. There is no operator action to force-clear; today the only workaround is a service restart, which also loses warm caches.

### Scope

- Add `POST /api/circuit-breaker/clear` with body `{ "reason": str }`, protected by `require_operator_token`.
- Flip `circuit_breaker_active` to false, clear `circuit_breaker_reason`, and record the clear event to persistence + telemetry.
- Add a "Clear circuit breaker" button to the circuit breaker banner in `src/ui/index.html`, visible only when the banner is in the tripped state **and** an operator token is present.

### Non-Goals

- No change to the circuit-breaker trip logic. If conditions still warrant a trip, the next evaluation cycle will re-trip it within 60s; operators are expected to understand that behavior.
- No per-rule clear (volatility vs drawdown vs cooldown). The breaker is single-bit today; this ticket does not change that.

### Current Anchors

- [src/bot.py:128-129](/home/lekim/Projects/akibaCryptobot/src/bot.py) — `self.circuit_breaker_active`, `self.circuit_breaker_reason`
- [src/bot.py:855-871](/home/lekim/Projects/akibaCryptobot/src/bot.py) — trip/untrip logic
- [src/api/routes/status.py](/home/lekim/Projects/akibaCryptobot/src/api/routes/status.py) — existing `/circuit-breaker` read route

### Target Files

- `src/api/routes/status.py` (or a new `src/api/routes/control.py`) — new POST route
- `src/bot.py` — add `clear_circuit_breaker(reason, actor="operator")` method
- `src/ui/index.html` — banner button
- `tests/api/test_status_routes.py` — route tests
- `docs/RISK_MANAGEMENT.md` — document the manual clear behavior

### Data Contracts

Request:

```json
{ "reason": "vol spike stale, 15m close back below 1.5σ" }
```

Response (200):

```json
{
  "cleared": true,
  "previous_reason": "volatility_exceeded",
  "cleared_at": "2026-04-16T12:00:00+00:00",
  "actor": "operator"
}
```

Response (200, not tripped):

```json
{ "cleared": false, "reason": "circuit_breaker_not_active" }
```

### Implementation Notes

- The clear is advisory. The next trading cycle's risk check may re-trip within 60s — that is correct behavior. The UI should make this expectation visible: "Breaker cleared. The next risk-check cycle may re-trip if conditions persist."
- The `clear_circuit_breaker` method on `AkibaCryptoBot` must be idempotent: clearing a not-active breaker returns `cleared: false` rather than raising.
- Persist a breaker-event row (type=`cleared`, actor, reason, timestamp) so the audit history shows both trips and clears.

### Failure Modes

- Operator clears, cycle re-trips → expected; UI shows the new trip reason.
- Persistence write fails → log warning; state change still applied (breaker state is the source of truth, the audit row is best-effort).

### Observability

- Log at INFO: `circuit_breaker.cleared` with previous reason and actor.
- Metric: `circuit_breaker_cleared_total`.
- Telegram notification on clear (distinct from trip notification).

### Tests

- Unit: `clear_circuit_breaker` when not active → `cleared=False`; when active → flips state and emits event.
- Integration: route returns 200 in both states; 403 without token.
- Regression: after clear, if the same risk condition persists, the next evaluation cycle re-trips (verify via mocked clock advance).

### Rollback Plan

- Remove the UI button and the route; `circuit_breaker_active` behavior is unchanged.

### Definition of Done

- Operator can clear a stale trip from the UI.
- Clear events show up in persistence and Telegram.
- Re-trip-within-60s behavior verified in tests.

## Initiative D — In-App Backtest UI

### Problem

`BacktestEngine` is a mature async component (`src/backtesting/engine.py:54`), but it is only driven from the CLI (`backtest_runner.py`). Operators who want to validate a strategy change must shell into a dev box. FreqUI's in-app backtest is the feature teams cite most often when asked why they stay in FreqUI.

### Scope

- Add `POST /api/backtest` (protected) that accepts strategy name, symbol list, timerange, initial balance, and returns a `job_id`.
- Run the backtest in a background task (`asyncio.create_task`) owned by a process-local `BacktestJobRegistry`.
- Add `GET /api/backtest/{job_id}` that returns status (`pending|running|completed|failed`) and, if completed, the full `BacktestResult` as JSON.
- Add `GET /api/backtest` returning the N most recent jobs (bounded, in-memory).
- Add a "Backtest" tab/panel to `src/ui/index.html` that posts the form, polls the job, and renders the result (trades table, equity curve sparkline, metrics grid).

### Non-Goals

- No on-disk job persistence. Jobs live in the process; a restart loses them. (If operators want persistence, they can export the result JSON from the UI.)
- No concurrent backtests. The registry enforces one in-flight job at a time; subsequent submissions 429 with `Retry-After`. Backtests are CPU-heavy and we don't want them competing with the live trading loop.
- No hyperparameter-sweep UI. Single-strategy, single-timerange only in this ticket.
- No plot configurator. The frontend will render the trades and the equity curve from the existing `BacktestResult.equity_curve` — nothing custom per strategy.

### Current Anchors

- [src/backtesting/engine.py:54](/home/lekim/Projects/akibaCryptobot/src/backtesting/engine.py) — `BacktestEngine`
- [src/backtesting/engine.py:14-51](/home/lekim/Projects/akibaCryptobot/src/backtesting/engine.py) — `BacktestTrade`, `BacktestResult` dataclasses
- [src/strategies/base_strategy.py](/home/lekim/Projects/akibaCryptobot/src/strategies/base_strategy.py) — strategy loading via `StrategyManager.add_strategy_by_name`

### Target Files

- `src/api/routes/backtest.py` (new) — the three endpoints + registry
- `src/backtesting/job_registry.py` (new) — in-memory job tracker with bounded history
- `src/ui/index.html` — new panel
- `tests/api/test_backtest_routes.py` (new)
- `docs/API_REFERENCE.md` — document the routes
- `docs/BACKTESTING.md` — add a "via dashboard" section

### Data Contracts

`POST /api/backtest` request:

```json
{
  "strategy": "ensemble",
  "symbols": ["BTCUSDT", "ETHUSDT"],
  "timerange": "2026-01-01:2026-04-01",
  "initial_balance": 10000,
  "fee": 0.001
}
```

`POST /api/backtest` response (202):

```json
{ "job_id": "bt_2026-04-16T12-00-00_ab12", "status": "pending" }
```

`GET /api/backtest/{job_id}` response (in-flight):

```json
{
  "job_id": "bt_2026-04-16T12-00-00_ab12",
  "status": "running",
  "progress_pct": 42.0,
  "submitted_at": "2026-04-16T12:00:00+00:00"
}
```

`GET /api/backtest/{job_id}` response (completed): the full `BacktestResult` serialized, plus `status: "completed"` and `duration_ms`. `BacktestTrade` and `datetime` fields serialize to ISO-8601.

### Implementation Notes

- `BacktestJobRegistry` holds at most 20 jobs (configurable). Older jobs are evicted when new ones submit. No disk spill.
- One concurrent job. A second `POST` while one is running returns 429 with `Retry-After: 30` and the in-flight `job_id`.
- The background task catches **all** exceptions from `run_backtest` and records them on the job as `status=failed` with an error message — a backtest crash must not take down the API process.
- OHLCV data fetching: reuse the existing historical-data loader used by `backtest_runner.py`. Do not reach through the live `binance_client` from inside the backtest request path.
- Progress reporting: backtest engine currently has no progress hook. Add a minimal callback `on_progress(pct: float)` that the job registry wires up. If the refactor of `run_backtest` is too large for this ticket, accept "running / completed" only and defer `progress_pct` to a follow-up.
- The UI renders the equity curve with the same lightweight canvas sparkline already used in the main dashboard — no new chart library.

### Failure Modes

- Strategy name not found → 400.
- Timerange invalid or > 2 years → 400.
- Data fetch fails → job transitions to `failed` with error detail.
- Process restarts mid-job → job is lost (documented non-goal); UI poll returns 404.

### Observability

- Log: `backtest.submitted` / `.completed` / `.failed` with `job_id`, `strategy`, `symbols`, `duration_ms`.
- Metric: `backtest_jobs_total{outcome=completed|failed}`, `backtest_duration_ms` histogram.
- Optional Telegram notification on completion (opt-in via config; off by default — backtests can be noisy).

### Tests

- Unit: registry evicts oldest when capacity reached; 429 when job in flight; 404 for unknown `job_id`.
- Integration: submit a tiny timerange against a known fixture dataset, poll until completed, assert `BacktestResult` shape.
- Regression: backtest exception surfaces as `failed` job, not a 500 on the poll endpoint.

### Rollback Plan

- The three routes are new and behind operator-token auth. Remove the route include from the FastAPI app; the rest of the system is unaffected.
- If `run_backtest` instability bleeds into the main process, the 429 single-concurrency guard limits blast radius to one degraded request at a time.

### Definition of Done

- Operator can submit a backtest from the dashboard and view results without leaving the page.
- Result JSON is identical in shape to the CLI output (same `BacktestResult` serialization).
- No backtest run can crash the API process.

## Initiative E — Favicon Trade Counter

### Problem

When the dashboard is one of many browser tabs, there is no way to tell from the tab strip whether anything is happening. FreqUI draws the open-trade count into the favicon.

### Scope

- Generate a favicon client-side via `<canvas>`, re-drawing whenever the open-position count changes.
- Color-code: green for "trading active with positions", grey for "no positions", red for "circuit breaker active" or "bot paused".
- Pure frontend change in `src/ui/index.html`. No backend, no auth.

### Non-Goals

- No notification sounds, no title-bar flashing, no push notifications. Those duplicate Telegram.
- No per-symbol indicators. The favicon is a single integer + one color.

### Current Anchors

- `src/ui/index.html` — existing positions table and pause/resume state polling

### Target Files

- `src/ui/index.html` — ~50 LoC of vanilla JS, one `<canvas>` helper.

### Data Contracts

No new API. Read from existing `/api/status` and `/api/trades` (already polled by the dashboard).

### Implementation Notes

- Redraw the favicon on every status poll tick, but only when the rendered state (count + color) has actually changed — cheap equality check to avoid DOM churn.
- Fallback: if `<canvas>` is unavailable (very old browsers), skip the redraw. The static favicon stays.

### Failure Modes

- `/api/status` fails → favicon shows last known state, not blank. The status banner already surfaces the error.

### Observability

- None. Pure cosmetic.

### Tests

- Manual only. Open dashboard, open a paper position, confirm favicon updates. Trip circuit breaker, confirm color changes.

### Rollback Plan

- Remove the JS block. Static favicon returns.

### Definition of Done

- Favicon shows correct count in the tab strip.
- Color reflects bot state (active / paused / breaker tripped).

## Cross-Cutting Concerns

### Operator-token conventions

- One header: `X-Operator-Token`. One env var: `AKIBA_OPERATOR_TOKEN`. No alternates.
- Rotation: changing the env var requires a service restart. Document that explicitly in `docs/DEPLOYMENT.md`.
- Dev ergonomics: if the env var is unset, mutating UI controls render as disabled with a tooltip pointing at the docs. They do not 500.

### Frontend state patterns

- No framework. Each initiative adds one top-level `const` module object to `index.html` with its own `init(rootEl)`, `render(state)`, `destroy()` hooks.
- Shared fetch helper lives in a single place (Initiative A). Every subsequent initiative uses it.

### Explicitly excluded FreqUI features

These were considered and rejected for this track:

| FreqUI feature | Why excluded |
|---|---|
| Plot configurator | Assumes a single strategy-owned dataframe with plot-config metadata; our strategies don't expose one, and adding it would creep into the strategy contract. |
| Candlestick chart | Doubles dashboard page weight. Operators who want candles have TradingView. |
| Theme toggle | One-person-evening work, no operational value. Defer indefinitely. |
| Pair-lock management UI | We don't implement pair locks. If/when we do (per `docs/RISK_MANAGEMENT.md`), this becomes a follow-up. |
| Pairlist testing UI | We have a fixed pairs config; no dynamic pairlist generator to test. |
| Timezone selector | All timestamps are UTC by convention; per-operator timezone is a display preference, not an operator control. |
| In-app notification center | Duplicates Telegram, which is already the canonical operator notification surface. |
| Data download UI | Historical data is managed via `make` targets and CI; not an interactive operator action. |
| JWT + per-user accounts | We are single-tenant; shared capability token is the right fit. |

### Deploy implications

- Setting `AKIBA_OPERATOR_TOKEN` on the server is a prerequisite for Initiatives B, C, D. Coordinate with `docs/DEPLOYMENT.md` before shipping Initiative A so the env var reaches the server ahead of the UI that needs it.
- No new dependencies. No build step. The dashboard stays a single static HTML file served by the existing FastAPI static route.

## Tests (track-wide)

- Every initiative adds route-level tests in `tests/api/`.
- Every mutating endpoint has a `test_{route}_requires_operator_token` case using the existing `require_operator_token` fixture pattern.
- No initiative lands without a "what happens under 403" case in the UI JS, validated manually.

## Rollback Plan (track-wide)

- Each initiative is a single PR with a single feature flag or route include, revertible without touching the others.
- Sequence is designed so reverting a later initiative never breaks an earlier one.

## Definition of Done (track-wide)

- All five initiatives merged to master.
- `docs/API_REFERENCE.md` reflects the new routes.
- `docs/DEPLOYMENT.md` documents the operator token lifecycle.
- `AKIBA_OPERATOR_TOKEN` is set on the deployed paper-runtime environment.
- `make check` green.

# AkibaCryptoBot Documentation

Welcome to the AkibaCryptoBot documentation. Use the guides below to understand, configure, deploy, and extend the bot.

---

## Current deployment

See [Freqtrade paper deployment](FREQTRADE.md) for the replacement runtime,
private FreqUI access, public portfolio, storage and rollback procedure.
The legacy custom engine is retained for reference.

## Guides

| Document | Description |
|---|---|
| [Architecture](ARCHITECTURE.md) | System overview, component diagram, trading cycle walkthrough, directory structure |
| [Configuration](CONFIGURATION.md) | All config options (`config.json`, `.env`) with types, defaults, and descriptions |
| [Strategies](STRATEGIES.md) | Strategy system, built-in strategies (Momentum, Ensemble, FreqAI ML), creating custom strategies |
| [Risk Management](RISK_MANAGEMENT.md) | Position sizing, stop loss, trailing stops, circuit breakers, correlation analysis |
| [API Reference](API_REFERENCE.md) | REST endpoints, WebSocket streams, request/response formats |
| [Telegram Commands](TELEGRAM_COMMANDS.md) | Bot commands, setup guide, automatic notifications |
| [Backtesting](BACKTESTING.md) | Backtest engine, walk-forward analysis, interpreting results |
| [Reconciliation](ARCHITECTURE.md#reconciliation) | How the bot stays in sync with actual exchange balances |
| [Deployment](DEPLOYMENT.md) | Local dev, Docker, CI/CD pipeline, cloud deployment |
| [Refactor Specs](specs/PHASE_1_EVENT_RUNTIME_SPEC.md) | Event-driven runtime refactor specs and AI task templates |
| [Phase 2 Market Data Spec](specs/PHASE_2_MARKET_DATA_SHADOW_SPEC.md) | Websocket-first market-data runtime, bootstrap, and shadow-mode validation |
| [Phase 3 Execution Spec](specs/PHASE_3_EXECUTION_POSITION_SPEC.md) | Execution service, position state, native protection, and fill-driven lifecycle |
| [Phase 4 Risk/Persistence Spec](specs/PHASE_4_RISK_PERSISTENCE_SPEC.md) | Runtime risk gating, warm-path persistence, telemetry, and runtime cutover preparation |
| [Phase 5 Signal/Strategy Spec](specs/PHASE_5_SIGNAL_STRATEGY_SPEC.md) | Runtime signal generation, strategy contracts, indicator caching, and entry-flow cutover |
| [Phase 6 Runtime Cutover Spec](specs/PHASE_6_RUNTIME_CUTOVER_SPEC.md) | Runtime ownership modes, bot-supervisor cutover, and controlled legacy-loop carve-out |
| [Phase 7 Runtime Compatibility Spec](specs/PHASE_7_RUNTIME_COMPATIBILITY_SPEC.md) | Runtime projection layer, runtime-aware API/Telegram surfaces, and paper-primary operator rollout |
| [Phase 8 Paper-Primary Activation Spec](specs/PHASE_8_PAPER_PRIMARY_ACTIVATION_SPEC.md) | Runtime rollout config, paper-primary activation, rollback control, and operator rollout visibility |
| [Phase 9 Paper Default and Live Guards Spec](specs/PHASE_9_PAPER_DEFAULT_AND_LIVE_GUARDS_SPEC.md) | Deployment profiles for paper runtime rollout, startup guardrails for live deployments, and paper-default cutover |
| [Phase 10 Profile-Aware Deploy Spec](specs/PHASE_10_PROFILE_AWARE_DEPLOY_SPEC.md) | Profile-aware deploy verification, deterministic deploy output, and paper-promotion runbook support |
| [Phase 11 Live Runtime Activation Spec](specs/PHASE_11_LIVE_RUNTIME_ACTIVATION_SPEC.md) | Reviewed live rollout profiles, live canary progression, and runtime activation guardrails |
| [Phase 12 Low-Latency Event Plane Spec](specs/PHASE_12_LOW_LATENCY_EVENT_PLANE_SPEC.md) | Hot-path latency budgets, incremental event processing, and runtime event capture/replay |
| [Phase 13 Order Book and Replay Spec](specs/PHASE_13_ORDER_BOOK_AND_REPLAY_SPEC.md) | Local order-book state, depth capture/replay, and microstructure feature groundwork |
| [Phase 14 Native Hot Path Spec](specs/PHASE_14_NATIVE_HOT_PATH_SPEC.md) | Native control-plane boundary, IPC contracts, and shadow validation for hot-path extraction |
| [Phase 15 HFT Infrastructure and Strategy Spec](specs/PHASE_15_HFT_INFRASTRUCTURE_AND_STRATEGY_SPEC.md) | HFT operating modes, latency SLOs, strategy families, and infrastructure/runbook requirements |
| [Phase 18 Runtime Trading Correctness Spec](specs/PHASE_18_RUNTIME_TRADING_CORRECTNESS_SPEC.md) | Carried-position hydration, restart recovery, runtime reconciliation, and runtime-primary correctness criteria |
| [Phase 19 Live Approval and Audit Spec](specs/PHASE_19_LIVE_APPROVAL_AND_AUDIT_SPEC.md) | Explicit live promotion approval, rollout audit history, and minimal operator control for live runtime release |
| [Phase 20 Live Runtime Execution Spec](specs/PHASE_20_LIVE_RUNTIME_EXECUTION_SPEC.md) | Live canary ladder execution, live-stage soak criteria, training restrictions, and rollback drills |
| [Phase 21 Low-Latency Trading Product Spec](specs/PHASE_21_LOW_LATENCY_TRADING_PRODUCT_SPEC.md) | Order-book capture/replay productization, first microstructure-aware paper strategies, and latency/fill-quality reporting |
| [Phase 22 Native Hot Path Activation Spec](specs/PHASE_22_NATIVE_HOT_PATH_ACTIVATION_SPEC.md) | First real native-side canary, native market-data/execution shadow validation, and operator-facing mismatch handling |
| [Operator Dashboard Enhancements Spec](specs/OPERATOR_DASHBOARD_ENHANCEMENTS_SPEC.md) | FreqUI-inspired operator UI track: auth wiring, force-exit per position, circuit-breaker reset, in-app backtests, favicon trade counter |
| [LLM Agent Iteration Workflow](LLM_AGENT_ITERATION_WORKFLOW.md) | Reusable protocol for phased, spec-driven, worker-assisted development and deployment |

---

## Optional Microstructure Research

The microstructure shadow loop subscribes to order-book and trade streams and
evaluates a research strategy. It never submits orders and is disabled in the
paper deployment because its output was not connected to execution. When
enabled for a specific, time-bounded experiment, its counters and recent
decisions are available in `/api/status` under
`runtime_telemetry.microstructure`. They stay in bounded memory and are not
written to SQLite. A research decision is not an executed trade.

See [Configuration → runtime.microstructure](CONFIGURATION.md#runtimemicrostructure)
for the experimental settings.

---

## Quick Start

```bash
# 1. Install
git clone https://github.com/micheal-donald/akibaCryptobot.git
cd akibaCryptobot
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env with your Binance API keys

# 3. Run (testnet by default)
python start_bot.py --testnet

# 4. Access dashboard
open http://localhost:8000
```

---

## Additional Resources

| Document | Description |
|---|---|
| [FreqAI Integration](freqai_integration.md) | FreqAI ML framework integration — feature engineering, custom strategies, model persistence |
| [AI Task Spec Template](specs/AI_TASK_SPEC_TEMPLATE.md) | Reusable ticket template for spec-driven and AI-assisted implementation |

# AkibaCryptoBot Documentation

Welcome to the AkibaCryptoBot documentation. Use the guides below to understand, configure, deploy, and extend the bot.

---

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
| [LLM Agent Iteration Workflow](LLM_AGENT_ITERATION_WORKFLOW.md) | Reusable protocol for phased, spec-driven, worker-assisted development and deployment |

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

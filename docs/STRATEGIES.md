# Trading Strategies

> Production uses [Freqtrade paper trading](FREQTRADE.md). That guide
> defines the replacement engine, configuration, API, risk rules and deployment.
> The custom-engine reference below is retained for historical development.

AkibaCryptoBot uses a pluggable strategy system. Every strategy inherits from `BaseStrategy`, and the `StrategyManager` handles registration, selection, and signal dispatch.

## Operating Modes

The documentation and operator expectations should distinguish three different trading regimes:

| Mode | Typical cadence | Strategy style | Operator focus |
|---|---|---|---|
| **Standard algo** | Candle or fixed-cycle scans | Momentum, ensemble, and other timeframe-driven strategies | Correctness, stability, risk control, and explainability |
| **Low-latency runtime** | Event-driven, websocket-fed, rollout-aware | Runtime-managed entries/exits, replay-backed validation, and microstructure-adjacent research | Feed freshness, queue lag, rollout health, and restart safety |
| **HFT** | Venue-near, latency-budgeted, queue-sensitive | Market making, inventory-aware quoting, order-flow alpha, microstructure stat-arb | Latency SLOs, kill-switch response, fill quality, and infrastructure discipline |

Current platform state:

- Standard algo strategies are supported today.
- The low-latency runtime exists and can be rolled out in paper/live stages.
- The native hot-path boundary is in place for future extraction.
- The repo is not yet a true HFT platform; HFT is a future operating regime and should be documented as such.

When evaluating a strategy or an operator surface, use the operating mode to decide what “healthy” means:

- Standard algo: successful signal generation, sensible risk controls, and reliable cycle completion.
- Low-latency runtime: fresh market data, bounded queue lag, clean rollout health, and benign shadow divergence.
- HFT: latency SLO compliance, order-book fidelity, fast anomaly handling, and venue-near execution discipline.

## HFT Strategy Families

Phase 15 identifies the first HFT-oriented strategy families as future implementation targets. They are not current production capabilities.

| Family | Description | Why it differs from current algo strategies |
|---|---|---|
| **Market making** | Passive bid/ask quoting around a local book, with inventory control and spread capture as the primary edge | Requires queue-sensitive order placement, fill modeling, and fast inventory/risk response rather than candle signals |
| **Inventory-aware quoting** | Market making that dynamically skews quotes based on current inventory and adverse-selection risk | Needs tighter execution feedback loops and inventory state than the current indicator-driven bot |
| **Short-horizon order-flow alpha** | Uses recent trade imbalance, aggression, and microstructure pressure over very short horizons | Depends on order-flow features and replay fidelity, not slow timeframe indicators |
| **Microstructure stat-arb** | Exploits short-lived relative-value relationships in order-book and trade-flow data | Requires multi-venue or multi-instrument microstructure analysis and a replay model that can preserve queue effects |

These families should be treated as distinct from the current candle/timeframe strategies:

- **Momentum / ensemble / FreqAI** remain standard algo strategies.
- **Low-latency runtime** research can use microstructure features and replay, but is still not HFT by itself.
- **HFT families** should only be considered once the platform can meet latency discipline, kill-switch expectations, and replay quality requirements documented elsewhere in the repo.

Operator expectation:

- If a strategy is candle-driven, it belongs in the standard algo lane.
- If a strategy depends on queue position, order-flow timing, or spread capture, it belongs in the low-latency or HFT lane.
- If a strategy cannot be replayed with queue/fill realism, it should not be described as HFT-ready.

## Phase 21 Microstructure Shadow Strategy

Phase 21 adds a first-pass paper-shadow contract for microstructure-aware research in `src/strategies/microstructure/`.

The current contract is intentionally small:

- `MicrostructureShadowStrategyContract` defines `evaluate(snapshot, features, decision_latency_ms=...)`
- `MicrostructureDecision` carries action, confidence, execution style, and feature context
- `PaperShadowMicrostructureStrategy` evaluates spread, depth imbalance, trade pressure, and latency budget

Operating assumptions:

- this strategy is not a candle/indicator strategy
- it runs against captured/replayed order-book data and, when
  `runtime.microstructure.enabled` is set, against live websocket depth/trade
  streams through `src/services/microstructure_shadow_service.py`
- it should run in paper shadow before any ownership cutover

Live paper-shadow wiring:

- the runtime subscribes partial-book depth and trade streams per configured symbol
- every depth update refreshes a local `OrderBookService` book and produces a
  `MicrostructureFeatureVector` (spread, depth imbalance, trade pressure)
- strategy decisions are logged, counted, and persisted as
  `microstructure.decision` events with decision latency (in-process compute)
  and pipeline latency (websocket receive to decision) so fill-quality and
  latency reports can be built offline
- no orders are placed from this path; it is measurement-only

Signal interpretation:

- `buy` means bullish imbalance or flow on a narrow spread
- `sell` means bearish imbalance or flow on a narrow spread
- `passive` execution style is the default because Phase 21 is measuring fill quality and spread capture

The strategy contract is not an HFT claim. It is the first productized low-latency research surface.

---

## Strategy Hierarchy

```mermaid
classDiagram
    class BaseStrategy {
        <<abstract>>
        +generate_signal(symbol, ohlcv, price)*
        +calculate_indicators(ohlcv)*
        +should_exit_position(symbol, position, price, ohlcv)
        +calculate_position_size(balance, strength, price)
        +validate_signal(signal)
        +warmup(symbol, ohlcv)
    }

    class MomentumStrategy {
        +generate_signal()
        +_calculate_signal_scores()
        +_calculate_stop_loss()
        +_calculate_take_profit()
        +should_exit_position()
    }

    class BaseFreqAIStrategy {
        <<abstract>>
        +feature_engineering_standard()*
        +set_freqai_targets()*
        +generate_signal()
        +train_model()
    }

    class MLFreqAIStrategy {
        +feature_engineering_standard()
        +set_freqai_targets()
        +train_model()
        +save_model()
        +load_model()
    }

    class EnsembleStrategy {
        +generate_signal()
        +should_exit_position()
        +train_models()
        +load_models()
    }

    class StrategyManager {
        +add_strategy(name, strategy)
        +set_active_strategy(name)
        +generate_signal(symbol, ohlcv, price)
        +get_all_performance()
    }

    BaseStrategy <|-- MomentumStrategy
    BaseStrategy <|-- BaseFreqAIStrategy
    BaseStrategy <|-- EnsembleStrategy
    BaseFreqAIStrategy <|-- MLFreqAIStrategy
    StrategyManager o-- BaseStrategy
```

---

## Signal Types

| Signal | Meaning |
|---|---|
| `BUY` | Open a long position |
| `SELL` | Open a short position |
| `HOLD` | No action |
| `CLOSE_LONG` | Exit an existing long |
| `CLOSE_SHORT` | Exit an existing short |

Each signal carries a **strength** value (0.0–1.0) that influences position sizing. A signal must meet `min_signal_strength` (default `0.45`) to be acted upon.

---

## Built-in Strategies

### 1. MomentumStrategy

A technical-analysis strategy that combines multiple indicators into a composite score.

**BUY conditions** (each adds to the buy score):
- RSI between `rsi_oversold` and `rsi_overbought` (+0.2)
- MACD line > Signal line (+0.25)
- Price below middle Bollinger Band (+0.15)
- Stochastic %K < `stoch_oversold` (+0.15)
- ADX > `adx_threshold` (+0.1)
- Volume above `min_volume_ratio` × average (+0.15)

**SELL conditions** (mirror logic):
- RSI > `rsi_overbought` (+0.2)
- MACD line < Signal line (+0.25)
- Price above upper Bollinger Band (+0.15)
- Stochastic %K > `stoch_overbought` (+0.15)
- ADX > `adx_threshold` (+0.1)
- Volume above average (+0.15)

**Dynamic exit logic:**
- ATR-based stop loss / take profit
- Indicator reversal detection (RSI flip, MACD crossover)
- Bollinger Band breakout exits

**Required history:** 200 candles

---

### 2. EnsembleStrategy

Combines Momentum and FreqAI ML signals using weighted voting.

| Component | Weight | Type |
|---|---|---|
| MomentumStrategy | 40% | Technical Analysis |
| MLFreqAIStrategy | 60% | Machine Learning |

**Signal generation:**
1. Each sub-strategy generates its own signal independently
2. BUY signals contribute `+weight × strength`, SELL signals contribute `−weight × strength`
3. Net score > agreement threshold → BUY; net score < −threshold → SELL
4. Final strength = weighted average of contributing signals

If FreqAI is disabled or its model for a pair is missing or expired, its
weight is redistributed to the available components. With the default
one-component agreement requirement, momentum-only fallback keeps the
momentum signal's strength instead of reducing it to 40%. A usable ML
component returning HOLD still retains its weight; this does not relax
consensus for ordinary HOLD votes or an explicit two-component requirement.

**Exit logic:** Exit if a majority of sub-strategies agree on exiting.

**Minimum agreement threshold:** 0.3 (configurable)

---

### 3. MLFreqAIStrategy (FreqAI)

A LightGBM-based classifier that predicts future price direction.

**Feature engineering (30+ features):**
- RSI (14), MACD (line, signal, histogram), Bollinger Bands (upper, middle, lower, %B, width)
- Stochastic %K/%D, ADX, ATR, OBV
- Price change percentages (1, 3, 5, 10 periods)
- Rolling volatility (10, 20 periods)
- Volume ratio vs 20-period average
- SMA/EMA crossover signals

**Target:** Future price direction over `lookahead_period` candles (default 5)
- Label `1` (buy) if future return > +1%
- Label `-1` (sell) if future return < −1%
- Label `0` (hold) otherwise

**Training:** `train_model()` trains on historical data with 80/20 train/test split. Model is saved to `models/<SYMBOL>_freqai.joblib` with a timestamp for expiry tracking.

**Model expiry:** Models older than `model_expiry_hours` (default 48) are flagged and skipped. The ensemble uses its remaining available components.

---

## How to Select a Strategy

Set the `strategy` key in `config/config.json`:

```json
{
  "trading": {
    "strategy": "ensemble"
  }
}
```

Or override via CLI:

```bash
python start_bot.py --strategy momentum
python start_bot.py --strategy ensemble
```

---

## Creating a Custom Strategy

1. Create a new file `src/strategies/my_strategy.py`
2. Inherit from `BaseStrategy`
3. Implement the required abstract methods

```python
from strategies.base_strategy import BaseStrategy, TradingSignal, SignalType
from indicators.technical_indicators import TechnicalIndicators

class MyStrategy(BaseStrategy):
    def __init__(self, config):
        super().__init__(config)
        self.name = "MyStrategy"

    def calculate_indicators(self, ohlcv_data):
        """Add your indicators to the dataframe."""
        df = ohlcv_data.copy()
        df["rsi"] = TechnicalIndicators.rsi(df["close"], period=14)
        df["sma_50"] = TechnicalIndicators.sma(df["close"], period=50)
        return df

    def generate_signal(self, symbol, ohlcv_data, current_price):
        """Return a TradingSignal or None."""
        df = self.calculate_indicators(ohlcv_data)
        latest = df.iloc[-1]

        if latest["rsi"] < 30 and current_price > latest["sma_50"]:
            return TradingSignal(
                signal_type=SignalType.BUY,
                strength=0.7,
                price=current_price,
                timestamp=datetime.now(),
                stop_loss=current_price * 0.98,
                take_profit=current_price * 1.04,
            )
        return None

    def get_required_history(self):
        return 100
```

3. Register your strategy by adding it to `_load_strategies()` in `bot.py`, or use `StrategyManager.add_strategy("my_strategy", MyStrategy(config))`.

---

## Available Indicators

The `TechnicalIndicators` class provides static methods for all supported indicators:

| Category | Indicators |
|---|---|
| **Trend** | SMA, EMA, Hull MA, KAMA, ADX, Ichimoku Cloud, SuperTrend |
| **Momentum** | RSI, MACD, Stochastic, Williams %R, CCI |
| **Volatility** | Bollinger Bands, ATR |
| **Volume** | OBV, VWAP, MFI |
| **Pattern** | Support/Resistance detection, Trend detection, Divergence detection |

# Performance Metrics System

## Overview

The Performance Metrics System provides comprehensive tracking and analysis of trading bot performance, including risk-adjusted metrics, trade analytics, and real-time reporting.

## Features Implemented

### 1. Core Metrics Tracking

#### Performance Metrics
- **Total P&L**: Absolute and percentage returns
- **Win Rate**: Percentage of profitable trades
- **Profit Factor**: Ratio of gross profits to gross losses
- **Expectancy**: Average profit/loss per trade
- **Win/Loss Ratio**: Average win size vs average loss size

#### Risk-Adjusted Metrics
- **Sharpe Ratio**: Risk-adjusted return measure accounting for volatility
- **Sortino Ratio**: Risk-adjusted return focusing on downside deviation
- **Maximum Drawdown**: Largest peak-to-trough decline
- **Drawdown Duration**: Time spent in drawdown periods

#### Trade Analytics
- **Entry/Exit Tracking**: Complete trade lifecycle recording
- **Strategy Performance**: Per-strategy performance breakdown
- **Equity Curve**: Historical balance tracking
- **Daily Returns**: Period-over-period return calculation

### 2. Data Structures

#### Trade Object
```python
@dataclass
class Trade:
    symbol: str
    side: str
    entry_price: float
    exit_price: float
    amount: float
    entry_time: datetime
    exit_time: datetime
    pnl: float
    pnl_percent: float
    exit_reason: str
    strategy: str
    fees: float
    metadata: Dict
```

#### PerformanceMetricsTracker
Main class that handles:
- Trade entry/exit recording
- Balance updates
- Metrics calculations
- Report generation
- Data export (DataFrame, CSV)

### 3. Integration with Bot

The performance tracker is integrated into the main bot at key points:

**Trade Entry** ([bot.py:326-333](../src/bot.py)):
```python
self.performance_tracker.record_trade_entry(
    symbol=symbol,
    side=side,
    amount=amount,
    entry_price=entry_price,
    strategy=strategy_name,
    metadata={'stop_loss': stop_loss, 'take_profit': take_profit}
)
```

**Trade Exit** ([bot.py:221-226](../src/bot.py)):
```python
completed_trade = self.performance_tracker.record_trade_exit(
    symbol=symbol,
    exit_price=exit_price,
    exit_reason=reason,
    fees=fees
)
```

**Balance Sync** ([bot.py:137-143](../src/bot.py)):
- Automatically syncs exchange balance with tracker every cycle
- Updates equity curve and calculates daily returns

### 4. Telegram Commands

Three new commands added for performance monitoring:

#### `/performance` - Quick Summary
Shows key metrics at a glance:
- Current balance and P&L
- Total trades and win rate
- Profit factor
- Maximum drawdown

#### `/metrics` - Detailed Metrics
Comprehensive breakdown including:
- Balance history
- Trade statistics (winners/losers, avg win/loss)
- Risk-adjusted metrics (Sharpe, Sortino, Profit Factor)
- Drawdown analysis
- Open positions count

#### `/report` - Full Report
Formatted ASCII report with all metrics in organized sections:
```
╔══════════════════════════════════════════════════════════════╗
║           AKIBA CRYPTO BOT - PERFORMANCE REPORT              ║
╚══════════════════════════════════════════════════════════════╝

📊 BALANCE & RETURNS
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
  Initial Balance:      $10,000.00
  Current Balance:      $10,250.00
  Total P&L:            $250.00
  Total Return:         2.50%
...
```

## Usage Examples

### Basic Usage

```python
from performance.metrics_tracker import PerformanceMetricsTracker

# Initialize tracker
tracker = PerformanceMetricsTracker(
    initial_balance=10000.0,
    risk_free_rate=0.02  # 2% annual risk-free rate
)

# Record trade entry
tracker.record_trade_entry(
    symbol='BTC/USDT',
    side='buy',
    amount=0.1,
    entry_price=50000.0,
    strategy='momentum'
)

# Record trade exit
trade = tracker.record_trade_exit(
    symbol='BTC/USDT',
    exit_price=52000.0,
    exit_reason='take_profit',
    fees=5.2
)

print(f"Trade P&L: ${trade.pnl:.2f}")
print(f"Trade Return: {trade.pnl_percent:.2f}%")
```

### Get Metrics Summary

```python
# Get comprehensive metrics
metrics = tracker.get_metrics_summary()

print(f"Win Rate: {metrics['win_rate']:.1f}%")
print(f"Sharpe Ratio: {metrics['sharpe_ratio']:.3f}")
print(f"Max Drawdown: {metrics['max_drawdown_percent']:.2f}%")
```

### Strategy Performance

```python
# Get performance by strategy
momentum_perf = tracker.get_strategy_performance('momentum')
print(f"Momentum Strategy Win Rate: {momentum_perf['win_rate']:.1f}%")

# Get all strategies
all_strategies = tracker.get_all_strategies_performance()
for strategy, perf in all_strategies.items():
    print(f"{strategy}: {perf['win_rate']:.1f}% win rate, ${perf['total_pnl']:.2f} P&L")
```

### Export Data

```python
# Export trades to DataFrame
trades_df = tracker.export_trades_to_dataframe()
trades_df.to_csv('trade_history.csv')

# Get equity curve
equity_df = tracker.get_equity_curve_data()
equity_df.plot(x='timestamp', y='balance', title='Equity Curve')

# Generate text report
report = tracker.generate_performance_report()
print(report)
```

## Configuration

Add to [config.json](../config/config.json):

```json
{
  "performance": {
    "risk_free_rate": 0.02,
    "enable_daily_reports": true,
    "report_schedule": "08:00"
  }
}
```

## Metrics Interpretation Guide

### Sharpe Ratio
- **< 1.0**: Poor risk-adjusted performance
- **1.0 - 2.0**: Good performance
- **> 2.0**: Excellent performance
- **> 3.0**: Outstanding performance

### Sortino Ratio
- Similar to Sharpe but focuses on downside risk
- Generally higher than Sharpe (only penalizes negative volatility)
- **> 2.0**: Good performance

### Profit Factor
- **< 1.0**: Strategy loses money
- **1.0 - 1.5**: Marginally profitable
- **1.5 - 2.0**: Good
- **> 2.0**: Excellent
- **> 3.0**: Outstanding

### Win Rate
- **< 40%**: Low (requires high win/loss ratio)
- **40% - 50%**: Average
- **50% - 60%**: Good
- **> 60%**: Excellent

### Maximum Drawdown
- **< 10%**: Low risk
- **10% - 20%**: Moderate risk
- **20% - 30%**: High risk
- **> 30%**: Very high risk

## Testing

Comprehensive test suite with 17 tests covering:
- Trade recording (entry/exit)
- Win/loss calculations
- All metrics calculations
- Strategy tracking
- Data export
- Report generation

Run tests:
```bash
pytest tests/test_performance_tracker.py -v
```

## Future Enhancements

Potential additions (see Priority 2-8 in main improvement plan):
1. **Walk-Forward Analysis**: Validate strategy performance over time
2. **Monte Carlo Simulation**: Assess strategy robustness
3. **Portfolio Heat Map**: Visualize correlated exposures
4. **Real-time Dashboard**: Web-based performance monitoring
5. **Automatic Report Scheduling**: Daily/weekly email/Telegram reports
6. **Trade Analysis**: Identify patterns in winning/losing trades
7. **Benchmark Comparison**: Compare vs BTC buy-and-hold, etc.

## Files Created

1. **[src/performance/metrics_tracker.py](../src/performance/metrics_tracker.py)** - Core tracker implementation (600+ lines)
2. **[src/performance/__init__.py](../src/performance/__init__.py)** - Module initialization
3. **[tests/test_performance_tracker.py](../tests/test_performance_tracker.py)** - Test suite (300+ lines)
4. **Updated [src/bot.py](../src/bot.py)** - Integration with main bot
5. **Updated [src/notifications/telegram_bot.py](../src/notifications/telegram_bot.py)** - New Telegram commands

## Dependencies

All dependencies already in project:
- pandas (data handling)
- numpy (calculations)
- python-telegram-bot (notifications)
- datetime (time tracking)

No additional installations required.

## Notes

- Metrics are cached for 60 seconds to improve performance
- Balance automatically syncs with exchange every trading cycle
- Trade fees are tracked and included in P&L calculations
- Supports both long and short positions
- Per-strategy performance tracking allows strategy comparison
- All timestamps are in local timezone
- Equity curve updates on every trade and balance change

---

**Status**: ✅ Complete and tested (17/17 tests passing)
**Priority**: High Impact - Provides essential visibility into bot performance
**Next Steps**: Implement Priority 2 (Walk-Forward Analysis) from improvement plan

# Quick Reference: Priority 2 & 3 Features

Quick reference for using walk-forward analysis and advanced risk management features.

---

## Testing Commands

```bash
# Validate configuration
python3 src/config_validator.py config/config.json

# Test walk-forward analyzer
python3 -m pytest tests/test_walkforward.py -v

# Run walk-forward analysis on strategy
python3 tools/walkforward_analysis.py --strategy momentum

# Start bot in testnet
python3 start_bot.py --testnet --debug
```

---

## Telegram Commands

| Command | Purpose |
|---------|---------|
| `/risk` | Risk management overview |
| `/correlations` | Asset correlation matrix |
| `/heatmap` | Portfolio risk visualization |
| `/performance` | Trading performance summary |

---

## Configuration Quick Setup

### Minimal Config (Conservative)

```json
{
  "advanced_risk": {
    "use_atr_sizing": false,
    "kelly_enabled": false,
    "correlation_threshold": 0.7,
    "max_correlated_positions": 2,
    "volatility_threshold": 2.0,
    "drawdown_threshold": 0.20
  }
}
```

### Full Config (Advanced)

```json
{
  "advanced_risk": {
    "use_atr_sizing": true,
    "atr_period": 14,
    "atr_multiplier": 2.0,
    "max_position_size_percent": 0.1,
    "kelly_enabled": true,
    "kelly_fractions_cap": 0.5,
    "min_trades_for_kelly": 20,
    "correlation_threshold": 0.7,
    "max_correlated_positions": 2,
    "volatility_threshold": 1.0,
    "drawdown_threshold": 0.15
  },
  "walkforward": {
    "n_splits": 5,
    "train_period": 180,
    "test_period": 60,
    "retrain_period": 2
  }
}
```

---

## Walk-Forward Interpretation

### Overfitting Score
- **< 0.2**: ✅ Low risk (good)
- **0.2-0.4**: ⚠️ Moderate risk (review)
- **> 0.4**: ❌ High risk (don't deploy)

### Sharpe Ratio
- **> 1.5**: ✅ Excellent
- **1.0-1.5**: ✅ Good
- **0.5-1.0**: ⚠️ Moderate
- **< 0.5**: ❌ Poor

### Max Drawdown
- **< 10%**: ✅ Low
- **10-20%**: ⚠️ Moderate
- **> 20%**: ❌ High

---

## File Locations

| File | Purpose |
|------|---------|
| `src/bot.py` | Main bot with integrations |
| `src/advanced_risk_management.py` | Risk management system |
| `src/backtesting/walkforward.py` | Walk-forward analyzer |
| `src/config_validator.py` | Config validation |
| `tools/walkforward_analysis.py` | CLI tool |
| `tests/test_walkforward.py` | WF tests (26 tests) |
| `tests/test_advanced_risk_TEMPLATE.py` | Risk tests template |
| `docs/PRIORITY_2_3_IMPLEMENTATION.md` | Full documentation |
| `docs/IMPLEMENTATION_SUMMARY.md` | Implementation summary |

---

## Common Issues

### Circuit Breaker Always Active
```json
// Increase thresholds
{
  "volatility_threshold": 2.0,
  "drawdown_threshold": 0.30
}
```

### All Positions Blocked (Correlation)
```json
// Loosen correlation limits
{
  "correlation_threshold": 0.85,
  "max_correlated_positions": 3
}
```

### Walk-Forward Analysis Fails
```bash
# Check data availability
python3 -c "
from exchange.binance_client import BinanceClient
import asyncio
async def test():
    client = BinanceClient(sandbox=True)
    data = await client.get_ohlcv('BTC/USDT', '1h', 100)
    print(f'Candles: {len(data)}')
asyncio.run(test())
"
```

---

## Documentation Links

- **Full Implementation Guide**: [PRIORITY_2_3_IMPLEMENTATION.md](PRIORITY_2_3_IMPLEMENTATION.md)
- **Implementation Summary**: [IMPLEMENTATION_SUMMARY.md](IMPLEMENTATION_SUMMARY.md)
- **Main Project README**: [../README.md](../README.md)
- **Project Instructions**: [../CLAUDE.md](../CLAUDE.md)

---

**Last Updated:** 2025-10-02

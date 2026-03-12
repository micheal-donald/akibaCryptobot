# Testing Checklist: Priority 2 & 3 Features

Use this checklist to systematically test all implemented features.

---

## Pre-Testing Setup

- [ ] Python 3.10+ installed
- [ ] All dependencies installed: `make install-dev`
- [ ] Environment variables set in `.env`:
  - [ ] `BINANCE_API_KEY`
  - [ ] `BINANCE_SECRET_KEY`
  - [ ] `TELEGRAM_BOT_TOKEN` (optional)
  - [ ] `TELEGRAM_CHAT_ID` (optional)
- [ ] Configuration file validated: `python3 src/config_validator.py config/config.json`

---

## 1. Configuration Validation Tests

### Test 1.1: Validate Default Config
```bash
python3 src/config_validator.py config/config.json
```
**Expected:**
- [ ] No ERROR level issues
- [ ] Report shows "Configuration is VALID"
- [ ] Lists any warnings (review and address if needed)

### Test 1.2: Validate Invalid Config
```bash
# Create test config with errors
cat > /tmp/test_config.json << 'EOF'
{
  "trading": {
    "pairs": [],
    "max_open_trades": -1
  },
  "advanced_risk": {
    "volatility_threshold": -0.5
  }
}
EOF

python3 src/config_validator.py /tmp/test_config.json
```
**Expected:**
- [ ] Shows ERROR for empty pairs list
- [ ] Shows ERROR for negative max_open_trades
- [ ] Shows ERROR for negative volatility_threshold
- [ ] Report shows "Configuration is INVALID"

---

## 2. Walk-Forward Analyzer Tests

### Test 2.1: Run Test Suite
```bash
python3 -m pytest tests/test_walkforward.py -v
```
**Expected:**
- [ ] 23 tests pass
- [ ] 3 tests fail (known cosmetic issues)
- [ ] No unexpected failures
- [ ] Execution time < 30 seconds

### Test 2.2: Check Test Coverage
```bash
python3 -m pytest tests/test_walkforward.py --cov=src/backtesting/walkforward --cov-report=html
```
**Expected:**
- [ ] Coverage > 90%
- [ ] HTML report generated in `htmlcov/`
- [ ] All core functions covered (check report)

### Test 2.3: Run Specific Tests
```bash
# Test Sharpe ratio calculation
python3 -m pytest tests/test_walkforward.py::test_calculate_sharpe_ratio -v

# Test overfitting score
python3 -m pytest tests/test_walkforward.py::test_calculate_overfitting_score -v

# Test max drawdown
python3 -m pytest tests/test_walkforward.py::test_calculate_max_drawdown -v
```
**Expected:**
- [ ] Each test passes independently
- [ ] Clear output showing test results

---

## 3. Walk-Forward CLI Tool Tests

### Test 3.1: Basic Analysis
```bash
python3 tools/walkforward_analysis.py \
    --strategy momentum \
    --symbols BTC/USDT \
    --start 2024-09-01 \
    --end 2024-10-01
```
**Expected:**
- [ ] Tool starts without errors
- [ ] Fetches historical data
- [ ] Runs analysis (may take 1-3 minutes)
- [ ] Shows results table with:
  - [ ] Total trades
  - [ ] Win rate
  - [ ] Profit factor
  - [ ] Sharpe ratio
  - [ ] Overfitting score
- [ ] Shows interpretation (✅/⚠️/❌ indicators)

### Test 3.2: Analysis with Output File
```bash
mkdir -p results
python3 tools/walkforward_analysis.py \
    --strategy momentum \
    --symbols BTC/USDT \
    --start 2024-09-01 \
    --end 2024-10-01 \
    --output results/test_wf.json
```
**Expected:**
- [ ] Analysis completes
- [ ] File created: `results/test_wf.json`
- [ ] File contains valid JSON
- [ ] JSON has all expected fields: `timestamp`, `strategy`, `symbols`, `results`, `fold_results`

### Test 3.3: Custom Parameters
```bash
python3 tools/walkforward_analysis.py \
    --strategy momentum \
    --symbols BTC/USDT ETH/USDT \
    --n-splits 3 \
    --train-period 60 \
    --test-period 20 \
    --start 2024-08-01 \
    --end 2024-10-01
```
**Expected:**
- [ ] Uses 3 splits (not default 5)
- [ ] Shows fold-by-fold results for 3 folds
- [ ] Analysis completes without errors

---

## 4. Bot Integration Tests

### Test 4.1: Bot Startup with Advanced Risk
```bash
python3 start_bot.py --testnet --debug 2>&1 | tee bot_test.log
# Let run for 2-3 minutes, then Ctrl+C
```
**Expected in logs:**
- [ ] "AdvancedRiskManager initialized"
- [ ] "Circuit breaker check" messages every cycle
- [ ] No errors during startup
- [ ] Trading cycle runs every 60 seconds

### Test 4.2: Check Circuit Breaker Integration
```bash
grep "circuit breaker" bot_test.log -i
```
**Expected:**
- [ ] Multiple "Circuit breaker check" messages
- [ ] Shows "should_halt = False" (or True if triggered)
- [ ] If triggered, shows reason

### Test 4.3: Check Correlation Updates
```bash
grep "correlation" bot_test.log -i
```
**Expected:**
- [ ] Correlation calculation messages
- [ ] Shows number of symbol pairs analyzed
- [ ] Updates every trading cycle

### Test 4.4: Check ATR Position Sizing (if enabled)
```bash
# First enable in config:
# "use_atr_sizing": true

python3 start_bot.py --testnet --debug 2>&1 | grep -i "atr"
```
**Expected (if use_atr_sizing=true):**
- [ ] "ATR-based position size" messages
- [ ] Shows ATR value
- [ ] Shows Kelly fraction
- [ ] Position size calculated using ATR

---

## 5. Circuit Breaker Manual Tests

### Test 5.1: Drawdown Circuit Breaker
```bash
# 1. Edit config.json, set very low threshold:
{
  "advanced_risk": {
    "drawdown_threshold": 0.01
  }
}

# 2. Start bot
python3 start_bot.py --testnet --debug

# 3. Wait for circuit breaker to trigger
```
**Expected:**
- [ ] Circuit breaker activates within 1-5 minutes
- [ ] Log shows: "🚨 CIRCUIT BREAKER ACTIVATED"
- [ ] Reason includes "drawdown exceeds threshold"
- [ ] New position scanning stops
- [ ] Telegram alert sent (if enabled)

**Cleanup:**
- [ ] Stop bot (Ctrl+C)
- [ ] Reset drawdown_threshold to normal (0.15-0.20)

### Test 5.2: Circuit Breaker Deactivation
```bash
# After Test 5.1, reset threshold and restart:
{
  "advanced_risk": {
    "drawdown_threshold": 0.30
  }
}

python3 start_bot.py --testnet --debug
```
**Expected:**
- [ ] Circuit breaker remains inactive
- [ ] Log shows: "✅ Circuit breaker deactivated" (if was previously active)
- [ ] Opportunity scanning resumes
- [ ] Telegram "deactivated" message sent (if enabled)

---

## 6. Correlation Diversification Tests

### Test 6.1: Configure Strict Correlation Limits
```json
{
  "advanced_risk": {
    "correlation_threshold": 0.6,
    "max_correlated_positions": 1
  },
  "trading": {
    "pairs": ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
  }
}
```
```bash
python3 start_bot.py --testnet --debug 2>&1 | tee correlation_test.log
# Let run for 5-10 minutes
```
**Expected:**
- [ ] Bot enters position in first symbol (e.g., BTC/USDT)
- [ ] Subsequent signals for correlated symbols (ETH) blocked
- [ ] Log shows: "Skipping ... - correlation check failed"

### Test 6.2: Verify Correlation Blocking Logic
```bash
grep "correlation" correlation_test.log -i
```
**Expected:**
- [ ] Correlation matrix calculated
- [ ] Shows high correlation between BTC/ETH (typically >0.7)
- [ ] Position blocked due to correlation limit

---

## 7. Telegram Command Tests

**Prerequisites:**
- [ ] Telegram bot configured
- [ ] Bot running: `python3 start_bot.py --testnet`

### Test 7.1: /risk Command
```
Send to Telegram: /risk
```
**Expected:**
- [ ] Bot responds within 5 seconds
- [ ] Shows circuit breaker status
- [ ] Shows position sizing parameters
- [ ] Shows correlation limits
- [ ] Shows circuit breaker thresholds
- [ ] Shows portfolio risk %

### Test 7.2: /correlations Command
```
Send to Telegram: /correlations
```
**Expected:**
- [ ] Shows correlation matrix (if bot has been running >5 min)
- [ ] OR shows "No correlation data available yet" (if just started)
- [ ] If shown, displays emoji indicators (🔴/🟡/🟢)
- [ ] Shows top 15 pairs sorted by correlation strength

### Test 7.3: /heatmap Command
```
Send to Telegram: /heatmap
```
**Expected:**
- [ ] Shows "No active positions" (if no positions)
- [ ] OR shows portfolio heatmap (if positions exist):
  - [ ] Total portfolio risk %
  - [ ] Asset exposures with visual bars
  - [ ] High correlation warnings (if any)

### Test 7.4: Test After Entering Position
```
1. Wait for bot to enter a position
2. Send: /heatmap
```
**Expected:**
- [ ] Shows asset exposure for active position
- [ ] Total risk % > 0
- [ ] Visual bars displayed

---

## 8. Performance Tests

### Test 8.1: Correlation Calculation Speed
```python
# Create test script: test_perf_correlation.py
import time
import asyncio
from exchange.binance_client import BinanceClient
from advanced_risk_management import AdvancedRiskManager

async def test():
    client = BinanceClient(sandbox=True)
    symbols = ['BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'AVAX/USDT', 'MATIC/USDT']

    # Fetch data
    price_data = {}
    for symbol in symbols:
        data = await client.get_ohlcv(symbol, '1h', 100)
        price_data[symbol] = data

    # Time correlation calculation
    risk_mgr = AdvancedRiskManager({})
    start = time.time()
    correlations = risk_mgr.calculate_correlations(price_data)
    duration = time.time() - start

    print(f"Correlation calculation: {duration:.3f}s for {len(symbols)} symbols")
    print(f"Calculated {len(correlations)} correlations")
    assert duration < 1.0, "Too slow!"

asyncio.run(test())
```
**Expected:**
- [ ] Completes in < 1 second for 5 symbols
- [ ] Calculates 10 correlation pairs (5 choose 2)

### Test 8.2: Circuit Breaker Check Speed
```python
# Add to test_perf_correlation.py
import numpy as np

# Time circuit breaker check
portfolio_history = [10000 * (1 + np.random.randn() * 0.01) for _ in range(100)]
start = time.time()
should_halt, reason = risk_mgr.check_circuit_breakers(price_data, portfolio_history)
duration = time.time() - start

print(f"Circuit breaker check: {duration:.3f}s")
assert duration < 0.1, "Too slow!"
```
**Expected:**
- [ ] Completes in < 100ms

---

## 9. Advanced Risk Manager Unit Tests (Optional)

### Test 9.1: Create Test File from Template
```bash
cp tests/test_advanced_risk_TEMPLATE.py tests/test_advanced_risk.py
```

### Test 9.2: Implement First 5 Tests
```bash
# Edit tests/test_advanced_risk.py
# Implement these 5 tests (easiest to start with):
# 1. test_atr_calculation
# 2. test_correlation_empty_data
# 3. test_correlation_single_symbol
# 4. test_circuit_breaker_empty_history
# 5. test_portfolio_heatmap_empty_positions

python3 -m pytest tests/test_advanced_risk.py -v
```
**Expected:**
- [ ] 5 tests pass
- [ ] No errors

### Test 9.3: Implement All Tests (Time Permitting)
```bash
# Implement all 25+ test cases
python3 -m pytest tests/test_advanced_risk.py -v --cov=src/advanced_risk_management
```
**Expected:**
- [ ] 25+ tests pass
- [ ] Coverage > 85%

---

## 10. Integration Smoke Tests

### Test 10.1: End-to-End Workflow
```bash
# 1. Validate config
python3 src/config_validator.py config/config.json

# 2. Run walk-forward analysis
python3 tools/walkforward_analysis.py --strategy momentum --symbols BTC/USDT

# 3. Start bot in testnet
python3 start_bot.py --testnet

# 4. Send Telegram commands
/risk
/correlations
/heatmap
/performance

# 5. Let run for 30 minutes, then stop
# Ctrl+C

# 6. Review logs
cat logs/akiba_crypto_bot.log | grep -i "circuit\|correlation\|atr"
```
**Expected:**
- [ ] All steps complete without errors
- [ ] Config validation passes
- [ ] Walk-forward shows results
- [ ] Bot starts successfully
- [ ] Telegram commands respond
- [ ] Logs show expected integration messages

---

## 11. Regression Tests

### Test 11.1: Existing Functionality Still Works
```bash
# Test basic bot functions haven't broken
python3 -m pytest tests/test_strategies.py -v
python3 -m pytest tests/test_config.py -v
```
**Expected:**
- [ ] All existing tests still pass
- [ ] No new failures introduced

### Test 11.2: Standard Position Sizing Still Works
```json
{
  "advanced_risk": {
    "use_atr_sizing": false,
    "kelly_enabled": false
  }
}
```
```bash
python3 start_bot.py --testnet --debug 2>&1 | grep -i "position size"
```
**Expected:**
- [ ] Position sizing works with standard method
- [ ] No ATR or Kelly mentioned in logs
- [ ] Positions calculated correctly

---

## Test Results Summary

Fill in after completing tests:

| Section | Tests Passed | Tests Failed | Notes |
|---------|--------------|--------------|-------|
| 1. Config Validation | __ / 2 | __ / 2 | |
| 2. Walk-Forward Tests | __ / 3 | __ / 3 | |
| 3. WF CLI Tool | __ / 3 | __ / 3 | |
| 4. Bot Integration | __ / 4 | __ / 4 | |
| 5. Circuit Breaker | __ / 2 | __ / 2 | |
| 6. Correlation | __ / 2 | __ / 2 | |
| 7. Telegram | __ / 4 | __ / 4 | |
| 8. Performance | __ / 2 | __ / 2 | |
| 9. Unit Tests (Opt) | __ / __ | __ / __ | |
| 10. Integration | __ / 1 | __ / 1 | |
| 11. Regression | __ / 2 | __ / 2 | |
| **TOTAL** | **__ / 25** | **__ / 25** | |

---

## Issues Found

Document any issues discovered during testing:

### Issue 1
- **Test:**
- **Expected:**
- **Actual:**
- **Severity:** Low / Medium / High
- **Fix Required:**

### Issue 2
- **Test:**
- **Expected:**
- **Actual:**
- **Severity:** Low / Medium / High
- **Fix Required:**

---

---

## 12. Asynchronous Concurrency Tests

### Test 12.1: Concurrent Order Placement
```bash
# Verify that multiple positions can be handled without blocking
python3 -m pytest tests/test_binance_connector.py -v
```
**Expected:**
- [ ] Uses `AsyncMock` for all exchange calls
- [ ] No `await` on non-coroutines
- [ ] Orders are processed asynchronously

### Test 12.2: WebSocket-REST Consistency
```bash
# Monitor logs for price updates from WS and REST
tail -f logs/akiba_crypto_bot.log | grep -E "WebSocket|Updated"
```
**Expected:**
- [ ] Prices update in real-time via WebSocket
- [ ] Fallback to REST OHLCV works if WS disconnects

---

## 13. Database Schema & Migration Tests

### Test 13.1: Legacy Timestamp Parsing
```bash
# Manually inject an ISO string timestamp into the DB
sqlite3 data/trades.db "INSERT INTO trades (symbol, timestamp, status) VALUES ('BTC/USDT', '2024-01-01T12:00:00', 'open')"

# Start bot
python3 start_bot.py --testnet
```
**Expected:**
- [ ] Bot starts without `'str' object cannot be interpreted as an integer` error
- [ ] `_parse_timestamp` correctly converts the ISO string to a datetime object
- [ ] Trade appears in `/trades` or `/api/trades`

### Test 13.2: Integer Timestamp Integrity
**Expected:**
- [ ] New trades are stored as **integers** (Unix Epoch)
- [ ] `sqlite3 data/trades.db "SELECT typeof(timestamp) FROM trades LIMIT 1"` returns `integer`

---

## 14. Reconciliation System Tests

### Test 14.1: Orphaned Asset Detection
1. Manually buy a small amount of a coin on Binance (outside the bot).
2. Trigger reconciliation: `/reconcile` on Telegram.

**Expected:**
- [ ] Bot detects the asset
- [ ] Telegram message: `🧐 ORPHAN ASSET: Found ... on exchange with no active DB record.`

### Test 14.2: Lost Trade detection
1. Create a manual "open trade" entry in `trades.db` for a symbol you don't own.
2. Trigger reconciliation.

**Expected:**
- [ ] Bot detects the missing asset
- [ ] Telegram message: `⚠️ LOST TRADE: ... (Expected X, Got 0.0)`

---

## Sign-Off (Updated)

- [ ] All critical tests passed
- [ ] Async refactor verified stable
- [ ] DB backward compatibility verified
- [ ] Ready for production deployment

**Last Updated:** 2026-03-12

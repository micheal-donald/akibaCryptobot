import pytest
from risk_management import RiskManager

def test_trailing_stop_ratchets_up_for_long():
    config = {
        "trailing_stop": True,
        "trailing_stop_positive": 0.05, # 5% to activate trailing stop
        "stop_loss_percent": 0.02 # 2% trailing stop distance
    }
    rm = RiskManager(config)
    
    rm.add_position(
        symbol="BTC/USDT",
        side="buy", # long
        amount=1.0,
        entry_price=10000.0,
        stop_loss=9000.0,
        take_profit=12000.0,
        order_id="1"
    )
    
    # Verify initial position
    pos = rm.positions["BTC/USDT"]
    assert pos["trailing_stop_price"] == pytest.approx(9000.0)
    
    # Price goes up to 10400 (4%). Still below the activation threshold (5% = 10500).
    updated = rm.update_trailing_stop("BTC/USDT", 10400.0)
    # The function returns the current_trailing_stop if it doesn't cross the threshold to make a new one
    assert updated == pytest.approx(9000.0) or updated is None
    assert rm.positions["BTC/USDT"]["trailing_stop_price"] == pytest.approx(9000.0)
    
    # Price goes up to 11000 (>5%). Activation threshold met.
    # New trailing stop should be 11000 * (1 - 0.02) = 11000 * 0.98 = 10780
    updated = rm.update_trailing_stop("BTC/USDT", 11000.0)
    assert updated == pytest.approx(10780.0)
    assert rm.positions["BTC/USDT"]["trailing_stop_price"] == pytest.approx(10780.0)
    
    # Price dips to 10800. Activation met, but new trailing = 10800 * 0.98 = 10584.
    # Since 10584 < 10780, it shouldn't update.
    updated = rm.update_trailing_stop("BTC/USDT", 10800.0)
    assert updated == pytest.approx(10780.0)
    assert rm.positions["BTC/USDT"]["trailing_stop_price"] == pytest.approx(10780.0)
    
    # Price skyrockets to 20000. Threshold met again.
    # New trailing stop should be 20000 * 0.98 = 19600.
    updated = rm.update_trailing_stop("BTC/USDT", 20000.0)
    assert updated == pytest.approx(19600.0)
    assert rm.positions["BTC/USDT"]["trailing_stop_price"] == pytest.approx(19600.0)

def test_trailing_stop_ratchets_down_for_short():
    config = {
        "trailing_stop": True,
        "trailing_stop_positive": 0.05, 
        "stop_loss_percent": 0.02
    }
    rm = RiskManager(config)
    
    rm.add_position(
        symbol="ETH/USDT",
        side="sell", # short
        amount=1.0,
        entry_price=3000.0,
        stop_loss=3300.0,
        take_profit=2000.0,
        order_id="1"
    )
    
    # Verify initial position
    pos = rm.positions["ETH/USDT"]
    assert pos["trailing_stop_price"] == pytest.approx(3300.0)
    
    # Price drops to 2800 (below 3000 * 0.95 = 2850 activation threshold).
    # New stop should be 2800 * 1.02 = 2856.
    updated = rm.update_trailing_stop("ETH/USDT", 2800.0)
    assert updated == pytest.approx(2856.0)
    assert rm.positions["ETH/USDT"]["trailing_stop_price"] == pytest.approx(2856.0)
    
    # Price bounces to 2850. New theoretical stop = 2850 * 1.02 = 2907.
    # Because 2907 > 2856, it shouldn't retreat.
    updated = rm.update_trailing_stop("ETH/USDT", 2850.0)
    assert updated == pytest.approx(2856.0)
    assert rm.positions["ETH/USDT"]["trailing_stop_price"] == pytest.approx(2856.0)

#!/usr/bin/env python3
"""
Test script to verify FreqAI strategy loading with StrategyManager
"""

import sys
import os

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_strategy_manager_loading():
    """Test StrategyManager loading of FreqAI strategies"""
    print("Testing StrategyManager loading of FreqAI strategies...")

    from strategies.base_strategy import StrategyManager

    # Create a minimal config
    config = {
        "exchange": {"sandbox": True},
        "trading": {"pairs": ["BTC/USDT"], "timeframes": {"primary": "1h"}},
        "risk_management": {},
        "notifications": {},
        "logging": {},
    }

    # Test StrategyManager directly
    strategy_manager = StrategyManager(config)

    # Try to load FreqAI strategies
    try:
        strategy_manager.add_strategy_by_name("freqai_example_freqai", config)
        if "freqai_example_freqai" in strategy_manager.strategies:
            print("✓ FreqAI example strategy loaded successfully by StrategyManager")
            print(
                f"  Strategy type: {type(strategy_manager.strategies['freqai_example_freqai'])}"
            )
        else:
            print("✗ FreqAI example strategy not added to manager")
    except Exception as e:
        print(f"✗ Failed to load FreqAI example strategy: {e}")
        import traceback

        traceback.print_exc()

    try:
        strategy_manager.add_strategy_by_name("freqai_ml_freqai", config)
        if "freqai_ml_freqai" in strategy_manager.strategies:
            print("✓ FreqAI ML strategy loaded successfully by StrategyManager")
            print(
                f"  Strategy type: {type(strategy_manager.strategies['freqai_ml_freqai'])}"
            )
        else:
            print("✗ FreqAI ML strategy not added to manager")
    except Exception as e:
        print(f"✗ Failed to load FreqAI ML strategy: {e}")
        import traceback

        traceback.print_exc()

    print(f"Available strategies: {list(strategy_manager.strategies.keys())}")

    # Test setting active strategy
    if "freqai_example_freqai" in strategy_manager.strategies:
        try:
            strategy_manager.set_active_strategy("freqai_example_freqai")
            if strategy_manager.active_strategy == "freqai_example_freqai":
                print("✓ Active strategy set to FreqAI example strategy")
            else:
                print("✗ Failed to set active strategy")
        except Exception as e:
            print(f"✗ Error setting active strategy: {e}")


if __name__ == "__main__":
    test_strategy_manager_loading()

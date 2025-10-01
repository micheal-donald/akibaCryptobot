#!/usr/bin/env python3
"""
Test script to verify FreqAI strategy loading
"""

import sys
import os
import json

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from strategies.base_strategy import StrategyManager


def test_strategy_loading():
    """Test that FreqAI strategies can be loaded"""
    print("Testing FreqAI strategy loading...")
    
    # Create a minimal config
    config = {
        "exchange": {
            "sandbox": True
        },
        "trading": {
            "pairs": ["BTC/USDT"],
            "timeframes": {
                "primary": "1h"
            }
        },
        "risk_management": {},
        "notifications": {},
        "logging": {}
    }
    
    # Test StrategyManager directly
    strategy_manager = StrategyManager(config)
    
    # Try to load FreqAI strategies
    try:
        strategy_manager.add_strategy_by_name('freqai_example_freqai', config)
        if 'freqai_example_freqai' in strategy_manager.strategies:
            print("✓ FreqAI example strategy loaded successfully")
        else:
            print("✗ FreqAI example strategy not added to manager")
    except Exception as e:
        print(f"✗ Failed to load FreqAI example strategy: {e}")
        import traceback
        traceback.print_exc()
    
    try:
        strategy_manager.add_strategy_by_name('freqai_ml_freqai', config)
        if 'freqai_ml_freqai' in strategy_manager.strategies:
            print("✓ FreqAI ML strategy loaded successfully")
        else:
            print("✗ FreqAI ML strategy not added to manager")
    except Exception as e:
        print(f"✗ Failed to load FreqAI ML strategy: {e}")
        import traceback
        traceback.print_exc()
    
    print(f"Available strategies: {list(strategy_manager.strategies.keys())}")


if __name__ == "__main__":
    test_strategy_loading()
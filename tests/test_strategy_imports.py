#!/usr/bin/env python3
"""
Test script to verify FreqAI strategy imports
"""

import sys
import os

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


def test_direct_imports():
    """Test direct imports of FreqAI strategies"""
    print("Testing direct imports of FreqAI strategies...")

    try:
        from strategies.freqai.example_freqai_strategy import ExampleFreqAIStrategy

        print("✓ ExampleFreqAIStrategy imported successfully")
    except Exception as e:
        print(f"✗ Failed to import ExampleFreqAIStrategy: {e}")
        import traceback

        traceback.print_exc()

    try:
        from strategies.freqai.ml_freqai_strategy import MLFreqAIStrategy

        print("✓ MLFreqAIStrategy imported successfully")
    except Exception as e:
        print(f"✗ Failed to import MLFreqAIStrategy: {e}")
        import traceback

        traceback.print_exc()


def test_strategy_instantiation():
    """Test instantiation of FreqAI strategies"""
    print("\nTesting instantiation of FreqAI strategies...")

    try:
        from strategies.freqai.example_freqai_strategy import ExampleFreqAIStrategy

        config = {"freqai": {"enabled": True}}
        strategy = ExampleFreqAIStrategy(config)
        print("✓ ExampleFreqAIStrategy instantiated successfully")
    except Exception as e:
        print(f"✗ Failed to instantiate ExampleFreqAIStrategy: {e}")
        import traceback

        traceback.print_exc()

    try:
        from strategies.freqai.ml_freqai_strategy import MLFreqAIStrategy

        config = {"freqai": {"enabled": True}}
        strategy = MLFreqAIStrategy(config)
        print("✓ MLFreqAIStrategy instantiated successfully")
    except Exception as e:
        print(f"✗ Failed to instantiate MLFreqAIStrategy: {e}")
        import traceback

        traceback.print_exc()


if __name__ == "__main__":
    test_direct_imports()
    test_strategy_instantiation()

#!/usr/bin/env python3
"""
Example script demonstrating how to use FreqAI strategies with akibaCryptobot
"""

import sys
import os
import asyncio
import pandas as pd
import numpy as np
from datetime import datetime

# Add src directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.strategies.freqai.example_freqai_strategy import ExampleFreqAIStrategy
from src.strategies.freqai.ml_freqai_strategy import MLFreqAIStrategy


def create_sample_data():
    """Create sample OHLCV data for demonstration"""
    dates = pd.date_range('2023-01-01', periods=1000, freq='h')
    # Create realistic price data with some trend and volatility
    prices = []
    current_price = 40000.0
    for i in range(len(dates)):
        # Add some randomness but with a slight upward trend
        change = np.random.normal(0.001, 0.02) + 0.0001  # 0.01% upward bias
        current_price = current_price * (1 + change)
        prices.append(current_price)
    
    return pd.DataFrame({
        'date': dates,
        'open': [p * np.random.uniform(0.995, 1.005) for p in prices],
        'high': [p * np.random.uniform(1.001, 1.01) for p in prices],
        'low': [p * np.random.uniform(0.99, 0.999) for p in prices],
        'close': prices,
        'volume': np.random.uniform(1000, 2000, len(dates))
    })


async def demonstrate_example_strategy():
    """Demonstrate the example FreqAI strategy"""
    print("=== Example FreqAI Strategy Demonstration ===")
    
    # Configuration
    config = {
        'freqai': {
            'enabled': True,
            'model': 'LightGBMClassifier',
            'identifier': 'example_model',
            'train_period': 200
        },
        'min_signal_strength': 0.6,
        'stop_loss_percent': 0.02,
        'take_profit_percent': 0.04,
        'rsi_period': 14,
        'macd_fast': 12,
        'macd_slow': 26,
        'macd_signal': 9,
        'backtesting': {
            'walkforward': {
                'enabled': True,
                'n_splits': 5,
                'train_period': 180,
                'test_period': 60,
                'retrain_period': 30
            }
        }
    }
    
    # Create strategy
    strategy = ExampleFreqAIStrategy(config)
    
    # Create sample data
    ohlcv_data = create_sample_data()
    current_price = ohlcv_data['close'].iloc[-1]
    
    print(f"Current price: {current_price:.2f}")
    print(f"Data points: {len(ohlcv_data)}")
    
    # Generate signal
    signal = await strategy.generate_signal("BTC/USDT", ohlcv_data, current_price)
    
    if signal:
        print(f"Generated signal: {signal.signal_type.value}")
        print(f"Strength: {signal.strength:.2f}")
        print(f"Price: {signal.price:.2f}")
        print(f"Stop Loss: {signal.stop_loss:.2f}")
        print(f"Take Profit: {signal.take_profit:.2f}")
    else:
        print("No signal generated")
    
    print()


async def demonstrate_ml_strategy():
    """Demonstrate the ML FreqAI strategy"""
    print("=== ML FreqAI Strategy Demonstration ===")
    
    # Configuration
    config = {
        'freqai': {
            'enabled': True,
            'model': 'RandomForest',
            'identifier': 'ml_model',
            'train_period': 200,
            'ml_params': {
                'lookahead_period': 5,
                'training_data_period': 500
            }
        },
        'min_signal_strength': 0.6,
        'stop_loss_percent': 0.02,
        'take_profit_percent': 0.04,
        'backtesting': {
            'walkforward': {
                'enabled': True,
                'n_splits': 5,
                'train_period': 180,
                'test_period': 60,
                'retrain_period': 30
            }
        }
    }
    
    # Create strategy
    strategy = MLFreqAIStrategy(config)
    
    # Create sample data
    ohlcv_data = create_sample_data()
    current_price = ohlcv_data['close'].iloc[-1]
    
    print(f"Current price: {current_price:.2f}")
    print(f"Data points: {len(ohlcv_data)}")
    
    # Train the model
    print("Training ML model...")
    await strategy.train_model("BTC/USDT", ohlcv_data)
    
    if strategy.is_model_trained:
        print("Model trained successfully")
        
        # Generate signal
        signal = await strategy.generate_signal("BTC/USDT", ohlcv_data, current_price)
        
        if signal:
            print(f"Generated signal: {signal.signal_type.value}")
            print(f"Strength: {signal.strength:.2f}")
            print(f"Price: {signal.price:.2f}")
            print(f"Stop Loss: {signal.stop_loss:.2f}")
            print(f"Take Profit: {signal.take_profit:.2f}")
        else:
            print("No signal generated")
    else:
        print("Failed to train model")
    
    print()


async def main():
    """Main demonstration function"""
    print("FreqAI Integration Demo for akibaCryptobot")
    print("=" * 50)
    print()
    
    # Demonstrate example strategy
    await demonstrate_example_strategy()
    
    # Demonstrate ML strategy
    await demonstrate_ml_strategy()
    
    print("Demo completed!")


if __name__ == "__main__":
    asyncio.run(main())
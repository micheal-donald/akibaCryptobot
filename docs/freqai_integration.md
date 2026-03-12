# FreqAI Integration Guide

This document explains how to use the FreqAI machine learning integration in akibaCryptobot.

## Overview

FreqAI is Freqtrade's integrated machine learning framework that enables users to leverage various ML models for cryptocurrency trading strategies. This integration allows akibaCryptobot to use advanced machine learning models for generating trading signals.

## Prerequisites

> [!IMPORTANT]
> AkibaCryptoBot uses its **own custom FreqAI-style ML implementation** built on LightGBM, scikit-learn, and joblib — it does **not** use or require the Freqtrade package.

All required dependencies are already included in `requirements.txt`. Install them with:

```bash
pip install -r requirements.txt
```

Key ML dependencies: `lightgbm`, `scikit-learn`, `numpy`, `pandas`.

## Available FreqAI Strategies

### 1. BaseFreqAIStrategy
The base class for all FreqAI strategies. Provides the foundation for integrating ML models with the akibaCryptobot framework.

### 2. ExampleFreqAIStrategy
A simple example strategy demonstrating FreqAI integration with rule-based predictions.

### 3. MLFreqAIStrategy
A more advanced strategy that uses scikit-learn machine learning models for predictions.

## Configuration

To enable FreqAI in your configuration, add the following to your `config.json`:

```json
{
  "freqai": {
    "enabled": true,
    "model": "LightGBMClassifier",
    "identifier": "akiba_freqai_model",
    "train_period": 200,
    "include_timeframes": ["1h", "4h"],
    "include_corr_pairs": [],
    "ml_params": {
      "lookahead_period": 5,
      "training_data_period": 1000
    }
  }
}
```

## Creating Custom FreqAI Strategies

To create a custom FreqAI strategy:

1. Create a new file in `src/strategies/freqai/`
2. Inherit from `BaseFreqAIStrategy`
3. Implement the required methods:
   - `feature_engineering_standard()`: Add features for the ML model
   - `set_freqai_targets()`: Define what the model should predict
   - `calculate_indicators()`: Calculate technical indicators
   - `_get_model_prediction()`: Get predictions from your ML model

## Feature Engineering

FreqAI strategies use a special syntax for features:

- `%` prefix: Features (e.g., `%-rsi`, `%-macd`)
- `&` prefix: Targets (e.g., `&-s_close`, `&-price_up`)

Example:
```python
def feature_engineering_standard(self, dataframe: pd.DataFrame, **kwargs) -> pd.DataFrame:
    dataframe['%-rsi'] = TechnicalIndicators.rsi(dataframe['close'], 14)
    dataframe['%-macd'] = TechnicalIndicators.macd(dataframe['close'])['macd']
    return dataframe
```

## Training Models

To train a model, use the `train_model()` method:

```python
await strategy.train_model("BTC/USDT", ohlcv_data)
```

## Using FreqAI Strategies

To use a FreqAI strategy in your bot:

1. Set the strategy in your config:
   ```json
   {
     "trading": {
       "strategy": "freqai_example_freqai"
     }
   }
   ```

2. The StrategyManager will automatically load the FreqAI strategy.

## Model Persistence

MLFreqAIStrategy supports saving and loading trained models:

```python
# Save model
strategy.save_model("models/my_model.joblib")

# Load model
strategy.load_model("models/my_model.joblib")
```

## Testing

Run FreqAI strategy tests with:

```bash
python -m pytest tests/test_freqai_strategies.py -v
```

## Performance Monitoring

FreqAI strategies include built-in performance monitoring and metrics tracking, which integrates with the existing akibaCryptobot performance tracking system.

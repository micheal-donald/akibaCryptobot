import pandas as pd
from typing import Dict, Optional
from datetime import datetime
import logging

from .base_strategy import BaseStrategy, TradingSignal, SignalType
from indicators.technical_indicators import calculate_all_indicators

logger = logging.getLogger(f"akiba_crypto_bot.{__name__}")


class MomentumStrategy(BaseStrategy):
    """
    Momentum-based trading strategy using multiple technical indicators

    Strategy Rules:
    BUY Signal:
    - RSI > 30 (not oversold) and RSI < 70 (not overbought)
    - MACD line > MACD signal line (bullish momentum)
    - Price > 20-period SMA (uptrend)
    - Stochastic %K > 20 (not oversold)
    - ADX > 25 (strong trend)

    SELL Signal:
    - RSI > 70 (overbought)
    - MACD line < MACD signal line (bearish momentum)
    - Price < 20-period SMA (downtrend)
    - Stochastic %K < 80 (not in extreme overbought)
    """

    def __init__(self, config: Dict):
        super().__init__(config)

        # Strategy-specific parameters
        self.rsi_oversold = config.get("rsi_oversold", 30)
        self.rsi_overbought = config.get("rsi_overbought", 70)
        self.stoch_oversold = config.get("stoch_oversold", 20)
        self.stoch_overbought = config.get("stoch_overbought", 80)
        self.adx_threshold = config.get("adx_threshold", 25)
        self.min_volume_ratio = config.get("min_volume_ratio", 1.2)  # Volume vs average

        # Signal strength weights
        self.indicator_weights = {
            "rsi": 0.2,
            "macd": 0.25,
            "trend": 0.25,
            "stochastic": 0.15,
            "adx": 0.15,
        }

        logger.info(
            f"MomentumStrategy initialized with RSI({self.rsi_oversold}, {self.rsi_overbought}), ADX>{self.adx_threshold}"
        )

    @classmethod
    def hyperopt_parameters(cls):
        from optimization.parameters import DecimalParameter, IntParameter

        return [
            DecimalParameter(
                name="min_signal_strength",
                default=0.35,
                low=0.2,
                high=0.8,
                step=0.05,
                space="buy",
            ),
            IntParameter(
                name="rsi_oversold",
                default=30,
                low=20,
                high=40,
                step=1,
                space="buy",
            ),
            IntParameter(
                name="rsi_overbought",
                default=70,
                low=60,
                high=85,
                step=1,
                space="sell",
            ),
            IntParameter(
                name="stoch_oversold",
                default=20,
                low=10,
                high=35,
                step=1,
                space="buy",
            ),
            IntParameter(
                name="stoch_overbought",
                default=80,
                low=65,
                high=90,
                step=1,
                space="sell",
            ),
            IntParameter(
                name="adx_threshold",
                default=25,
                low=15,
                high=40,
                step=1,
                space="buy",
            ),
            DecimalParameter(
                name="min_volume_ratio",
                default=1.2,
                low=0.8,
                high=2.0,
                step=0.1,
                space="buy",
            ),
        ]

    async def generate_signal(
        self, symbol: str, ohlcv_data: pd.DataFrame, current_price: float
    ) -> Optional[TradingSignal]:
        """Generate trading signal based on momentum indicators"""
        try:
            if len(ohlcv_data) < 50:
                logger.warning(
                    f"Insufficient data for {symbol}: {len(ohlcv_data)} bars"
                )
                return None

            # Calculate all technical indicators
            df_with_indicators = self.calculate_indicators(ohlcv_data)

            if df_with_indicators is None or len(df_with_indicators) == 0:
                return None

            # Get latest values from the last *completed* candle (avoiding live partial candle)
            latest = df_with_indicators.iloc[-2]

            # Check if we have all required indicators
            required_indicators = [
                "rsi",
                "macd",
                "macd_signal",
                "sma_20",
                "stoch_k",
                "adx",
            ]
            if not all(indicator in latest.index for indicator in required_indicators):
                logger.warning(f"Missing required indicators for {symbol}")
                return None

            # Calculate signal components
            signal_scores = self._calculate_signal_scores(latest, current_price)

            # Determine overall signal
            buy_score = signal_scores["buy_score"]
            sell_score = signal_scores["sell_score"]

            # Generate signal based on scores
            if buy_score > sell_score and buy_score > self.min_signal_strength:
                signal_type = SignalType.BUY
                strength = min(buy_score, 1.0)
            elif sell_score > buy_score and sell_score > self.min_signal_strength:
                signal_type = SignalType.SELL
                strength = min(sell_score, 1.0)
            else:
                logger.debug(
                    f"No signal for {symbol}: buy_score={buy_score:.2f}, "
                    f"sell_score={sell_score:.2f} (threshold={self.min_signal_strength})"
                )
                return None

            # Calculate stop loss and take profit
            stop_loss = self._calculate_stop_loss(current_price, signal_type, latest)
            take_profit = self._calculate_take_profit(
                current_price, signal_type, latest
            )

            # Create trading signal
            signal = TradingSignal(
                signal_type=signal_type,
                strength=strength,
                price=current_price,
                timestamp=datetime.now(),
                stop_loss=stop_loss,
                take_profit=take_profit,
                metadata={
                    "strategy": "momentum",
                    "buy_score": buy_score,
                    "sell_score": sell_score,
                    "rsi": latest["rsi"],
                    "macd": latest["macd"],
                    "macd_signal": latest["macd_signal"],
                    "adx": latest["adx"],
                    "stoch_k": latest["stoch_k"],
                    "trend": "up" if current_price > latest["sma_20"] else "down",
                },
            )

            # Validate signal
            if self.validate_signal(signal):
                self.log_signal(signal, symbol)
                return signal

            return None

        except Exception as e:
            logger.error(f"Error generating signal for {symbol}: {e}")
            return None

    def calculate_indicators(self, ohlcv_data: pd.DataFrame) -> pd.DataFrame:
        """Calculate technical indicators for the strategy"""
        return calculate_all_indicators(ohlcv_data)

    def _calculate_signal_scores(
        self, latest: pd.Series, current_price: float
    ) -> Dict[str, float]:
        """Calculate buy and sell signal scores based on indicators"""
        buy_score = 0.0
        sell_score = 0.0

        # RSI component
        rsi = latest["rsi"]
        if self.rsi_oversold < rsi < self.rsi_overbought:
            if rsi < 50:
                buy_score += self.indicator_weights["rsi"] * (
                    1 - (rsi - self.rsi_oversold) / (50 - self.rsi_oversold)
                )
            else:
                sell_score += self.indicator_weights["rsi"] * (
                    (rsi - 50) / (self.rsi_overbought - 50)
                )
        elif rsi <= self.rsi_oversold:
            buy_score += self.indicator_weights["rsi"] * 1.5  # Strong buy on oversold
        elif rsi >= self.rsi_overbought:
            sell_score += (
                self.indicator_weights["rsi"] * 1.5
            )  # Strong sell on overbought

        # MACD component
        macd = latest["macd"]
        macd_signal = latest["macd_signal"]
        if macd > macd_signal:
            buy_score += self.indicator_weights["macd"]
        else:
            sell_score += self.indicator_weights["macd"]

        # Trend component (SMA)
        sma_20 = latest["sma_20"]
        if current_price > sma_20:
            trend_strength = min(
                (current_price - sma_20) / sma_20 * 10, 1.0
            )  # Normalize
            buy_score += self.indicator_weights["trend"] * trend_strength
        else:
            trend_strength = min(
                (sma_20 - current_price) / current_price * 10, 1.0
            )  # Normalize
            sell_score += self.indicator_weights["trend"] * trend_strength

        # Stochastic component
        stoch_k = latest["stoch_k"]
        if stoch_k > self.stoch_oversold and stoch_k < self.stoch_overbought:
            if stoch_k < 50:
                buy_score += self.indicator_weights["stochastic"] * (1 - stoch_k / 50)
            else:
                sell_score += self.indicator_weights["stochastic"] * (
                    (stoch_k - 50) / 50
                )
        elif stoch_k <= self.stoch_oversold:
            buy_score += self.indicator_weights["stochastic"] * 1.2
        elif stoch_k >= self.stoch_overbought:
            sell_score += self.indicator_weights["stochastic"] * 1.2

        # ADX component (trend strength)
        adx = latest["adx"]
        if adx > self.adx_threshold:
            trend_multiplier = min(adx / 50, 1.0)  # Normalize ADX
            buy_score *= 1 + trend_multiplier * self.indicator_weights["adx"]
            sell_score *= 1 + trend_multiplier * self.indicator_weights["adx"]
        else:
            # Weak trend, reduce signal strength
            buy_score *= 0.7
            sell_score *= 0.7

        logger.debug(
            f"Scores for {latest.name if hasattr(latest, 'name') else 'latest'}: "
            f"RSI={latest['rsi']:.1f}, MACD={latest['macd']:.4f}, "
            f"Trend={'UP' if current_price > latest['sma_20'] else 'DOWN'}, "
            f"ADX={latest['adx']:.1f} | BUY={buy_score:.2f}, SELL={sell_score:.2f}"
        )

        return {"buy_score": buy_score, "sell_score": sell_score}

    def _calculate_stop_loss(
        self, current_price: float, signal_type: SignalType, latest: pd.Series
    ) -> float:
        """Calculate dynamic stop loss based on ATR"""
        try:
            atr = latest.get("atr", current_price * 0.02)  # Fallback to 2%

            if signal_type == SignalType.BUY:
                # For buy signals, stop loss below current price
                stop_loss = current_price - (atr * 2)  # 2 ATR below
            else:
                # For sell signals, stop loss above current price
                stop_loss = current_price + (atr * 2)  # 2 ATR above

            return stop_loss

        except Exception:
            # Fallback to percentage-based stop loss
            if signal_type == SignalType.BUY:
                return current_price * (1 - self.stop_loss_percent)
            else:
                return current_price * (1 + self.stop_loss_percent)

    def _calculate_take_profit(
        self, current_price: float, signal_type: SignalType, latest: pd.Series
    ) -> float:
        """Calculate dynamic take profit based on ATR"""
        try:
            atr = latest.get("atr", current_price * 0.02)  # Fallback to 2%

            if signal_type == SignalType.BUY:
                # For buy signals, take profit above current price
                take_profit = current_price + (atr * 3)  # 3 ATR above (1.5:1 ratio)
            else:
                # For sell signals, take profit below current price
                take_profit = current_price - (atr * 3)  # 3 ATR below

            return take_profit

        except Exception:
            # Fallback to percentage-based take profit
            if signal_type == SignalType.BUY:
                return current_price * (1 + self.take_profit_percent)
            else:
                return current_price * (1 - self.take_profit_percent)

    async def should_exit_position(
        self,
        symbol: str,
        current_position: Dict,
        current_price: float,
        ohlcv_data: pd.DataFrame,
    ) -> Optional[TradingSignal]:
        """Enhanced exit logic with indicator-based exits"""

        # First check basic stop loss / take profit
        basic_exit = await super().should_exit_position(
            symbol, current_position, current_price, ohlcv_data
        )
        if basic_exit:
            return basic_exit

        try:
            # Calculate indicators for exit signals
            df_with_indicators = self.calculate_indicators(ohlcv_data)
            if df_with_indicators is None or len(df_with_indicators) == 0:
                return None

            # Use the last completed candle for exit logic
            latest = df_with_indicators.iloc[-2]
            side = current_position.get("side")
            entry_price = current_position.get("entry_price")

            if not side or not entry_price:
                return None

            # Check for indicator-based exits
            if side == "buy":
                # Exit long position conditions
                if (
                    latest["rsi"] > 75
                    or (  # Very overbought
                        latest["macd"] < latest["macd_signal"]
                        and latest["stoch_k"] > 80
                    )
                    or current_price  # Momentum turning + overbought
                    < latest["sma_20"] * 0.98
                ):  # Break below trend with buffer
                    return TradingSignal(
                        signal_type=SignalType.CLOSE_LONG,
                        strength=0.8,
                        price=current_price,
                        timestamp=datetime.now(),
                        metadata={
                            "exit_reason": "indicator_based_exit",
                            "strategy": "momentum",
                        },
                    )

            else:  # sell/short position
                # Exit short position conditions
                if (
                    latest["rsi"] < 25
                    or (  # Very oversold
                        latest["macd"] > latest["macd_signal"]
                        and latest["stoch_k"] < 20
                    )
                    or current_price  # Momentum turning + oversold
                    > latest["sma_20"] * 1.02
                ):  # Break above trend with buffer
                    return TradingSignal(
                        signal_type=SignalType.CLOSE_SHORT,
                        strength=0.8,
                        price=current_price,
                        timestamp=datetime.now(),
                        metadata={
                            "exit_reason": "indicator_based_exit",
                            "strategy": "momentum",
                        },
                    )

            return None

        except Exception as e:
            logger.error(f"Error in enhanced exit logic for {symbol}: {e}")
            return None

    def get_required_history(self) -> int:
        """Get minimum historical data required for this strategy"""
        return max(
            100, self.lookback_period
        )  # Need at least 100 periods for indicators

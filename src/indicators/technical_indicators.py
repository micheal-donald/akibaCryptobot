import pandas as pd
import numpy as np
from typing import Dict, Tuple, Optional
import ta
from ta.utils import dropna
import logging

logger = logging.getLogger(__name__)


class TechnicalIndicators:
    """Collection of technical analysis indicators"""

    @staticmethod
    def sma(data: pd.Series, period: int) -> pd.Series:
        """Simple Moving Average"""
        return data.rolling(window=period).mean()

    @staticmethod
    def ema(data: pd.Series, period: int) -> pd.Series:
        """Exponential Moving Average"""
        return data.ewm(span=period).mean()

    @staticmethod
    def rsi(data: pd.Series, period: int = 14) -> pd.Series:
        """Relative Strength Index"""
        return ta.momentum.RSIIndicator(close=data, window=period).rsi()

    @staticmethod
    def macd(
        data: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
    ) -> Dict[str, pd.Series]:
        """MACD Indicator"""
        macd_indicator = ta.trend.MACD(
            close=data, window_fast=fast, window_slow=slow, window_sign=signal
        )
        return {
            "macd": macd_indicator.macd(),
            "macd_signal": macd_indicator.macd_signal(),
            "macd_histogram": macd_indicator.macd_diff(),
        }

    @staticmethod
    def bollinger_bands(
        data: pd.Series, period: int = 20, std_dev: int = 2
    ) -> Dict[str, pd.Series]:
        """Bollinger Bands"""
        bb_indicator = ta.volatility.BollingerBands(
            close=data, window=period, window_dev=std_dev
        )
        return {
            "bb_upper": bb_indicator.bollinger_hband(),
            "bb_middle": bb_indicator.bollinger_mavg(),
            "bb_lower": bb_indicator.bollinger_lband(),
            "bb_width": bb_indicator.bollinger_wband(),
            "bb_percent": bb_indicator.bollinger_pband(),
        }

    @staticmethod
    def stochastic(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        k_period: int = 14,
        d_period: int = 3,
    ) -> Dict[str, pd.Series]:
        """Stochastic Oscillator"""
        stoch_indicator = ta.momentum.StochasticOscillator(
            high=high, low=low, close=close, window=k_period, smooth_window=d_period
        )
        return {
            "stoch_k": stoch_indicator.stoch(),
            "stoch_d": stoch_indicator.stoch_signal(),
        }

    @staticmethod
    def atr(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
    ) -> pd.Series:
        """
        Calculate Average True Range (ATR) - a measure of market volatility.

        ATR is calculated by taking the average of true range values over a specified period.
        The true range is the greatest of the following:
        1. Current high minus current low
        2. Absolute value of current high minus previous close
        3. Absolute value of current low minus previous close

        Args:
            high: High prices as pandas Series
            low: Low prices as pandas Series
            close: Close prices as pandas Series
            period: Number of periods for moving average (default: 14)

        Returns:
            pandas Series with ATR values

        Raises:
            ValueError: If input data is invalid or insufficient
        """
        # Input validation
        if not all(isinstance(data, pd.Series) for data in [high, low, close]):
            raise ValueError("High, low, and close must be pandas Series")

        if len(high) != len(low) or len(high) != len(close):
            raise ValueError("High, low, and close series must have same length")

        if len(high) < period + 1:
            logger.warning(
                f"Insufficient data for ATR calculation. Required: {period + 1}, Available: {len(high)}"
            )
            return pd.Series([np.nan] * len(high), index=high.index, dtype=float)

        try:
            # Validate price data
            if high.isna().any() or low.isna().any() or close.isna().any():
                logger.warning(
                    "Input data contains NaN values. Filling with forward fill."
                )
                high = high.fillna(method="ffill")
                low = low.fillna(method="ffill")
                close = close.fillna(method="ffill")

            # Validate that high >= low for all periods
            if (high < low).any():
                logger.warning(
                    "Invalid price data: high price less than low price detected"
                )
                # Fix by swapping values where high < low
                mask = high < low
                high[mask], low[mask] = low[mask], high[mask]

            # Calculate True Range components
            tr1 = high - low
            tr2 = abs(high - close.shift(1))
            tr3 = abs(low - close.shift(1))

            # Get maximum of the three true range components
            true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

            # Calculate ATR using simple moving average
            atr = true_range.rolling(window=period).mean()

            # Ensure the result has the same index as input
            atr_result = pd.Series(atr, index=high.index, dtype=float)

            logger.debug(f"Successfully calculated ATR with period {period}")
            return atr_result

        except Exception as e:
            logger.error(f"Error calculating ATR: {e}")
            return pd.Series([np.nan] * len(high), index=high.index, dtype=float)

    @staticmethod
    def adx(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
    ) -> Dict[str, pd.Series]:
        """Average Directional Index"""
        # Validate input data
        if (
            len(high) < period * 2
            or high.isna().any()
            or low.isna().any()
            or close.isna().any()
        ):
            logger.warning("Insufficient or invalid data for ADX calculation")
            return {
                "adx": pd.Series(index=high.index, dtype=float),
                "adx_pos": pd.Series(index=high.index, dtype=float),
                "adx_neg": pd.Series(index=high.index, dtype=float),
            }

        # Check for zero price ranges
        price_range = high - low
        if (price_range == 0).any():
            logger.warning("Zero price ranges detected in ADX calculation")

        try:
            with np.errstate(divide="ignore", invalid="ignore"):
                adx_indicator = ta.trend.ADXIndicator(
                    high=high, low=low, close=close, window=period
                )
                return {
                    "adx": adx_indicator.adx().fillna(0),
                    "adx_pos": adx_indicator.adx_pos().fillna(0),
                    "adx_neg": adx_indicator.adx_neg().fillna(0),
                }
        except Exception as e:
            logger.error(f"Error calculating ADX: {e}")
            return {
                "adx": pd.Series(index=high.index, dtype=float),
                "adx_pos": pd.Series(index=high.index, dtype=float),
                "adx_neg": pd.Series(index=high.index, dtype=float),
            }

    @staticmethod
    def ichimoku(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        conversion_period: int = 9,
        base_period: int = 26,
        leading_span_b_period: int = 52,
        displacement: int = 26,
    ) -> Dict[str, pd.Series]:
        """Ichimoku Cloud"""
        ichimoku_indicator = ta.trend.IchimokuIndicator(
            high=high,
            low=low,
            close=close,
            window1=conversion_period,
            window2=base_period,
            window3=leading_span_b_period,
        )

        return {
            "conversion_line": ichimoku_indicator.ichimoku_conversion_line(),
            "base_line": ichimoku_indicator.ichimoku_base_line(),
            "leading_span_a": ichimoku_indicator.ichimoku_a(),
            "leading_span_b": ichimoku_indicator.ichimoku_b(),
        }

    @staticmethod
    def williams_r(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14
    ) -> pd.Series:
        """Williams %R"""
        return ta.momentum.WilliamsRIndicator(
            high=high, low=low, close=close, lbp=period
        ).williams_r()

    @staticmethod
    def cci(
        high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20
    ) -> pd.Series:
        """Commodity Channel Index"""
        return ta.trend.CCIIndicator(
            high=high, low=low, close=close, window=period
        ).cci()

    @staticmethod
    def obv(close: pd.Series, volume: pd.Series) -> pd.Series:
        """On Balance Volume"""
        return ta.volume.OnBalanceVolumeIndicator(
            close=close, volume=volume
        ).on_balance_volume()

    @staticmethod
    def vwap(
        high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series
    ) -> pd.Series:
        """Volume Weighted Average Price"""
        typical_price = (high + low + close) / 3
        return (typical_price * volume).cumsum() / volume.cumsum()

    @staticmethod
    def hull_ma(data: pd.Series, period: int = 20) -> pd.Series:
        """Hull Moving Average - Advanced trend following indicator"""
        try:
            half_period = int(period / 2)
            sqrt_period = int(np.sqrt(period))

            wma_half = TechnicalIndicators.wma(data, half_period)
            wma_full = TechnicalIndicators.wma(data, period)

            raw_hma = 2 * wma_half - wma_full
            hull_ma = TechnicalIndicators.wma(raw_hma, sqrt_period)

            return hull_ma
        except Exception as e:
            logger.warning(f"Error calculating Hull MA: {e}")
            return pd.Series(index=data.index, dtype=float)

    @staticmethod
    def wma(data: pd.Series, period: int) -> pd.Series:
        """Weighted Moving Average"""
        weights = np.arange(1, period + 1)
        return data.rolling(period).apply(
            lambda x: np.dot(x, weights) / weights.sum(), raw=True
        )

    @staticmethod
    def kaufman_ma(
        data: pd.Series, period: int = 10, fast_sc: int = 2, slow_sc: int = 30
    ) -> pd.Series:
        """Kaufman's Adaptive Moving Average (KAMA)"""
        try:
            change = abs(data - data.shift(period))
            volatility = abs(data - data.shift(1)).rolling(period).sum()

            # Avoid division by zero
            volatility = volatility.replace(0, np.nan)
            efficiency_ratio = change / volatility

            fast_sc = 2 / (fast_sc + 1)
            slow_sc = 2 / (slow_sc + 1)

            smoothing_constant = (efficiency_ratio * (fast_sc - slow_sc) + slow_sc) ** 2

            kama = pd.Series(index=data.index, dtype=float)
            kama.iloc[period] = data.iloc[period]

            for i in range(period + 1, len(data)):
                if not pd.isna(smoothing_constant.iloc[i]):
                    kama.iloc[i] = kama.iloc[i - 1] + smoothing_constant.iloc[i] * (
                        data.iloc[i] - kama.iloc[i - 1]
                    )
                else:
                    kama.iloc[i] = kama.iloc[i - 1]

            return kama
        except Exception as e:
            logger.warning(f"Error calculating KAMA: {e}")
            return pd.Series(index=data.index, dtype=float)

    @staticmethod
    def mfi(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        volume: pd.Series,
        period: int = 14,
    ) -> pd.Series:
        """Money Flow Index - Enhanced volume-price indicator"""
        try:
            typical_price = (high + low + close) / 3
            money_flow = typical_price * volume

            positive_flow = pd.Series(index=close.index, dtype=float)
            negative_flow = pd.Series(index=close.index, dtype=float)

            for i in range(1, len(typical_price)):
                if typical_price.iloc[i] > typical_price.iloc[i - 1]:
                    positive_flow.iloc[i] = money_flow.iloc[i]
                    negative_flow.iloc[i] = 0
                elif typical_price.iloc[i] < typical_price.iloc[i - 1]:
                    positive_flow.iloc[i] = 0
                    negative_flow.iloc[i] = money_flow.iloc[i]
                else:
                    positive_flow.iloc[i] = 0
                    negative_flow.iloc[i] = 0

            positive_mf = positive_flow.rolling(period).sum()
            negative_mf = negative_flow.rolling(period).sum()

            money_ratio = positive_mf / negative_mf
            money_ratio = money_ratio.replace([np.inf, -np.inf], np.nan)

            mfi = 100 - (100 / (1 + money_ratio))
            return mfi.fillna(50)

        except Exception as e:
            logger.warning(f"Error calculating MFI: {e}")
            return pd.Series(index=close.index, dtype=float)

    @staticmethod
    def supertrend(
        high: pd.Series,
        low: pd.Series,
        close: pd.Series,
        atr_period: int = 10,
        multiplier: float = 3.0,
    ) -> Dict[str, pd.Series]:
        """SuperTrend - Advanced trend following indicator"""
        try:
            # Calculate ATR
            atr = TechnicalIndicators.atr(high, low, close, atr_period)

            # Calculate basic upper and lower bands
            hl2 = (high + low) / 2
            upper_basic = hl2 + (multiplier * atr)
            lower_basic = hl2 - (multiplier * atr)

            # Calculate final upper and lower bands
            upper_final = pd.Series(index=close.index, dtype=float)
            lower_final = pd.Series(index=close.index, dtype=float)

            upper_final.iloc[0] = upper_basic.iloc[0]
            lower_final.iloc[0] = lower_basic.iloc[0]

            for i in range(1, len(close)):
                # Upper band logic
                if (
                    upper_basic.iloc[i] < upper_final.iloc[i - 1]
                    or close.iloc[i - 1] > upper_final.iloc[i - 1]
                ):
                    upper_final.iloc[i] = upper_basic.iloc[i]
                else:
                    upper_final.iloc[i] = upper_final.iloc[i - 1]

                # Lower band logic
                if (
                    lower_basic.iloc[i] > lower_final.iloc[i - 1]
                    or close.iloc[i - 1] < lower_final.iloc[i - 1]
                ):
                    lower_final.iloc[i] = lower_basic.iloc[i]
                else:
                    lower_final.iloc[i] = lower_final.iloc[i - 1]

            # Calculate SuperTrend
            supertrend = pd.Series(index=close.index, dtype=float)
            trend = pd.Series(index=close.index, dtype=int)

            supertrend.iloc[0] = lower_final.iloc[0]
            trend.iloc[0] = 1

            for i in range(1, len(close)):
                if trend.iloc[i - 1] == 1 and close.iloc[i] <= lower_final.iloc[i]:
                    supertrend.iloc[i] = lower_final.iloc[i]
                    trend.iloc[i] = 1
                elif trend.iloc[i - 1] == 1 and close.iloc[i] > lower_final.iloc[i]:
                    supertrend.iloc[i] = upper_final.iloc[i]
                    trend.iloc[i] = -1
                elif trend.iloc[i - 1] == -1 and close.iloc[i] >= upper_final.iloc[i]:
                    supertrend.iloc[i] = upper_final.iloc[i]
                    trend.iloc[i] = -1
                else:
                    supertrend.iloc[i] = lower_final.iloc[i]
                    trend.iloc[i] = 1

            return {
                "supertrend": supertrend,
                "trend": trend,
                "upper_band": upper_final,
                "lower_band": lower_final,
            }

        except Exception as e:
            logger.warning(f"Error calculating SuperTrend: {e}")
            return {
                "supertrend": pd.Series(index=close.index, dtype=float),
                "trend": pd.Series(index=close.index, dtype=int),
                "upper_band": pd.Series(index=close.index, dtype=float),
                "lower_band": pd.Series(index=close.index, dtype=float),
            }


class PatternRecognition:
    """Chart pattern recognition utilities"""

    @staticmethod
    def detect_support_resistance(
        data: pd.Series, window: int = 20, min_touches: int = 2
    ) -> Dict[str, list]:
        """Detect support and resistance levels"""
        highs = data.rolling(window=window).max()
        lows = data.rolling(window=window).min()

        # Find peaks and troughs
        peaks = []
        troughs = []

        for i in range(window, len(data) - window):
            if data.iloc[i] == highs.iloc[i]:
                peaks.append((i, data.iloc[i]))
            if data.iloc[i] == lows.iloc[i]:
                troughs.append((i, data.iloc[i]))

        # Group similar levels
        resistance_levels = PatternRecognition._group_levels(
            [p[1] for p in peaks], min_touches
        )
        support_levels = PatternRecognition._group_levels(
            [t[1] for t in troughs], min_touches
        )

        return {"resistance": resistance_levels, "support": support_levels}

    @staticmethod
    def _group_levels(levels: list, min_touches: int, tolerance: float = 0.02) -> list:
        """Group similar price levels"""
        if not levels:
            return []

        grouped = []
        levels = sorted(levels)

        current_group = [levels[0]]

        for level in levels[1:]:
            if abs(level - current_group[-1]) / current_group[-1] <= tolerance:
                current_group.append(level)
            else:
                if len(current_group) >= min_touches:
                    grouped.append(np.mean(current_group))
                current_group = [level]

        if len(current_group) >= min_touches:
            grouped.append(np.mean(current_group))

        return grouped

    @staticmethod
    def detect_trend(data: pd.Series, period: int = 20) -> str:
        """Detect overall trend direction"""
        if len(data) < period:
            return "neutral"

        recent_data = data.tail(period)
        slope = np.polyfit(range(len(recent_data)), recent_data.values, 1)[0]

        # Normalize slope by price
        normalized_slope = slope / data.iloc[-1]

        if normalized_slope > 0.01:  # 1% upward slope
            return "uptrend"
        elif normalized_slope < -0.01:  # 1% downward slope
            return "downtrend"
        else:
            return "neutral"

    @staticmethod
    def detect_divergence(
        price: pd.Series, indicator: pd.Series, period: int = 5
    ) -> str:
        """Detect bullish/bearish divergence"""
        if len(price) < period * 2 or len(indicator) < period * 2:
            return "none"

        # Get recent peaks and troughs
        price_recent = price.tail(period * 2)
        indicator_recent = indicator.tail(period * 2)

        price_trend = PatternRecognition.detect_trend(price_recent, period)
        indicator_trend = PatternRecognition.detect_trend(indicator_recent, period)

        if price_trend == "downtrend" and indicator_trend == "uptrend":
            return "bullish_divergence"
        elif price_trend == "uptrend" and indicator_trend == "downtrend":
            return "bearish_divergence"
        else:
            return "none"


class MarketStructure:
    """Market structure analysis tools"""

    @staticmethod
    def calculate_volatility(data: pd.Series, period: int = 20) -> pd.Series:
        """Calculate rolling volatility"""
        returns = data.pct_change()
        return returns.rolling(window=period).std() * np.sqrt(252)  # Annualized

    @staticmethod
    def calculate_volume_profile(
        close: pd.Series, volume: pd.Series, bins: int = 50
    ) -> Dict[str, np.ndarray]:
        """Calculate volume profile"""
        price_min, price_max = close.min(), close.max()
        price_bins = np.linspace(price_min, price_max, bins + 1)

        volume_profile = np.zeros(bins)

        for i in range(len(close)):
            bin_idx = np.digitize(close.iloc[i], price_bins) - 1
            bin_idx = max(0, min(bins - 1, bin_idx))
            volume_profile[bin_idx] += volume.iloc[i]

        return {
            "price_levels": (price_bins[:-1] + price_bins[1:]) / 2,
            "volume": volume_profile,
        }

    @staticmethod
    def find_poc(close: pd.Series, volume: pd.Series) -> float:
        """Find Point of Control (highest volume price level)"""
        volume_profile = MarketStructure.calculate_volume_profile(close, volume)
        max_volume_idx = np.argmax(volume_profile["volume"])
        return volume_profile["price_levels"][max_volume_idx]

    @staticmethod
    def calculate_market_sentiment(
        rsi: pd.Series, macd_histogram: pd.Series, stoch_k: pd.Series
    ) -> pd.Series:
        """Calculate composite market sentiment score"""
        # Normalize indicators to 0-1 range
        rsi_norm = rsi / 100

        # MACD histogram normalization (more complex)
        macd_norm = (macd_histogram - macd_histogram.min()) / (
            macd_histogram.max() - macd_histogram.min()
        )

        stoch_norm = stoch_k / 100

        # Composite sentiment (average of normalized indicators)
        sentiment = (rsi_norm + macd_norm + stoch_norm) / 3

        return sentiment


def calculate_all_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate all technical indicators for a DataFrame"""
    try:
        # Ensure we have required columns
        required_columns = ["open", "high", "low", "close", "volume"]
        if not all(col in df.columns for col in required_columns):
            logger.error(
                f"Missing required columns. Required: {required_columns}, Found: {df.columns.tolist()}"
            )
            return df

        # Clean the data
        df = dropna(df)

        if len(df) < 50:  # Need minimum data for indicators
            logger.warning("Insufficient data for indicator calculation")
            return df

        result_df = df.copy()

        # Moving averages
        result_df["sma_20"] = TechnicalIndicators.sma(df["close"], 20)
        result_df["sma_50"] = TechnicalIndicators.sma(df["close"], 50)
        result_df["ema_12"] = TechnicalIndicators.ema(df["close"], 12)
        result_df["ema_26"] = TechnicalIndicators.ema(df["close"], 26)

        # Momentum indicators
        result_df["rsi"] = TechnicalIndicators.rsi(df["close"])

        # MACD
        macd_data = TechnicalIndicators.macd(df["close"])
        result_df["macd"] = macd_data["macd"]
        result_df["macd_signal"] = macd_data["macd_signal"]
        result_df["macd_histogram"] = macd_data["macd_histogram"]

        # Bollinger Bands
        bb_data = TechnicalIndicators.bollinger_bands(df["close"])
        result_df["bb_upper"] = bb_data["bb_upper"]
        result_df["bb_middle"] = bb_data["bb_middle"]
        result_df["bb_lower"] = bb_data["bb_lower"]
        result_df["bb_width"] = bb_data["bb_width"]
        result_df["bb_percent"] = bb_data["bb_percent"]

        # Stochastic
        stoch_data = TechnicalIndicators.stochastic(df["high"], df["low"], df["close"])
        result_df["stoch_k"] = stoch_data["stoch_k"]
        result_df["stoch_d"] = stoch_data["stoch_d"]

        # Volatility
        result_df["atr"] = TechnicalIndicators.atr(df["high"], df["low"], df["close"])

        # Trend indicators - with data validation
        try:
            adx_data = TechnicalIndicators.adx(df["high"], df["low"], df["close"])
            result_df["adx"] = adx_data["adx"]
            result_df["adx_pos"] = adx_data["adx_pos"]
            result_df["adx_neg"] = adx_data["adx_neg"]
        except Exception as e:
            logger.warning(f"Error calculating ADX indicators: {e}")
            result_df["adx"] = pd.Series(index=df.index, dtype=float)
            result_df["adx_pos"] = pd.Series(index=df.index, dtype=float)
            result_df["adx_neg"] = pd.Series(index=df.index, dtype=float)

        # Volume indicators
        result_df["obv"] = TechnicalIndicators.obv(df["close"], df["volume"])

        # Additional indicators
        result_df["williams_r"] = TechnicalIndicators.williams_r(
            df["high"], df["low"], df["close"]
        )
        result_df["cci"] = TechnicalIndicators.cci(df["high"], df["low"], df["close"])

        # Market structure
        result_df["volatility"] = MarketStructure.calculate_volatility(df["close"])

        # Advanced indicators
        try:
            # Hull Moving Average
            result_df["hull_ma_20"] = TechnicalIndicators.hull_ma(df["close"], 20)
            result_df["hull_ma_50"] = TechnicalIndicators.hull_ma(df["close"], 50)

            # Kaufman's Adaptive Moving Average
            result_df["kama"] = TechnicalIndicators.kaufman_ma(df["close"])

            # Money Flow Index
            result_df["mfi"] = TechnicalIndicators.mfi(
                df["high"], df["low"], df["close"], df["volume"]
            )

            # SuperTrend
            supertrend_data = TechnicalIndicators.supertrend(
                df["high"], df["low"], df["close"]
            )
            result_df["supertrend"] = supertrend_data["supertrend"]
            result_df["supertrend_trend"] = supertrend_data["trend"]

            # Enhanced VWAP
            result_df["vwap"] = TechnicalIndicators.vwap(
                df["high"], df["low"], df["close"], df["volume"]
            )

        except Exception as e:
            logger.warning(f"Error calculating advanced indicators: {e}")

        # Market sentiment
        if all(
            col in result_df.columns for col in ["rsi", "macd_histogram", "stoch_k"]
        ):
            result_df["market_sentiment"] = MarketStructure.calculate_market_sentiment(
                result_df["rsi"], result_df["macd_histogram"], result_df["stoch_k"]
            )

        logger.info(
            f"Successfully calculated {len(result_df.columns) - len(df.columns)} technical indicators"
        )
        return result_df

    except Exception as e:
        logger.error(f"Error calculating indicators: {e}")
        return df

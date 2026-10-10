"""Independent implementation of ADXMomentum rules with explicit paper limits.

Reference: freqtrade/freqtrade-strategies, berlinguyinca/ADXMomentum.py.
Differences: 3% stop, 200 warmup bars, positive volume, cooldown and loss guards.
This variant has no established profitability and refuses live trading.
"""

import talib.abstract as ta
from pandas import DataFrame
from freqtrade.strategy import IStrategy


class AkibaADXPaper(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "1h"
    can_short = False
    startup_candle_count = 200
    process_only_new_candles = True
    minimal_roi = {"0": 0.01}
    stoploss = -0.03
    trailing_stop = False
    position_adjustment_enable = False
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    def version(self) -> str:
        return "paper-1"

    def bot_start(self, **kwargs) -> None:
        if self.config.get("dry_run") is not True:
            raise ValueError("AkibaADXPaper is restricted to dry-run/backtesting")
        if str(self.config.get("trading_mode", "spot")) != "spot":
            raise ValueError("AkibaADXPaper requires spot trading")

    @property
    def protections(self):
        return [
            {"method": "CooldownPeriod", "stop_duration_candles": 3},
            {
                "method": "StoplossGuard",
                "lookback_period_candles": 24,
                "trade_limit": 2,
                "stop_duration_candles": 6,
                "only_per_pair": False,
            },
        ]

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["plus_di"] = ta.PLUS_DI(dataframe, timeperiod=25)
        dataframe["minus_di"] = ta.MINUS_DI(dataframe, timeperiod=25)
        dataframe["mom"] = ta.MOM(dataframe, timeperiod=14)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["adx"] > 25)
            & (dataframe["mom"] > 0)
            & (dataframe["plus_di"] > 25)
            & (dataframe["plus_di"] > dataframe["minus_di"])
            & (dataframe["volume"] > 0),
            ["enter_long", "enter_tag"],
        ] = (1, "adx_momentum")
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        dataframe.loc[
            (dataframe["adx"] > 25)
            & (dataframe["mom"] < 0)
            & (dataframe["minus_di"] > 25)
            & (dataframe["plus_di"] < dataframe["minus_di"])
            & (dataframe["volume"] > 0),
            ["exit_long", "exit_tag"],
        ] = (1, "momentum_reversal")
        return dataframe

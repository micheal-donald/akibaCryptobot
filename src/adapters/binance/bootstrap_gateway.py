"""Binance REST bootstrap adapter for normalized OHLCV candles."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

import pandas as pd

from adapters.binance.symbol_mapper import BinanceSymbolMapper
from domain.models import Candle
from exchange.binance_client import BinanceClient
from ports.exchange import MarketDataBootstrapGateway

_SYMBOL_MAPPER = BinanceSymbolMapper()


@runtime_checkable
class _OHLCVClientLike(Protocol):
    async def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 100,
    ) -> Any: ...


@dataclass(slots=True)
class BinanceBootstrapGateway(MarketDataBootstrapGateway):
    """Load and normalize Binance OHLCV history for runtime bootstrapping."""

    client: _OHLCVClientLike

    def __init__(
        self,
        client: _OHLCVClientLike | None = None,
        *,
        api_key: str = "",
        api_secret: str = "",
        sandbox: bool = True,
        market_type: str = "spot",
        binance_us: bool = False,
    ) -> None:
        if client is None:
            client = BinanceClient(
                api_key=api_key,
                secret_key=api_secret,
                sandbox=sandbox,
                market_type=market_type,
                binance_us=binance_us,
            )
        self.client = client

    async def fetch_ohlcv(
        self,
        symbol: str,
        interval: str,
        limit: int,
    ) -> list[Candle]:
        """Fetch historical candles and return normalized, typed objects."""

        client_symbol = self._normalize_client_symbol(symbol)
        raw_ohlcv = await self.client.get_ohlcv(
            client_symbol,
            timeframe=interval,
            limit=limit,
        )
        return self._normalize_ohlcv(raw_ohlcv)

    def _normalize_ohlcv(self, raw_ohlcv: Any) -> list[Candle]:
        if isinstance(raw_ohlcv, pd.DataFrame):
            return self._from_dataframe(raw_ohlcv)
        if isinstance(raw_ohlcv, (list, tuple)):
            return self._from_sequence(raw_ohlcv)
        raise TypeError(
            "unsupported OHLCV payload; expected pandas.DataFrame or sequence"
        )

    def _from_dataframe(self, frame: pd.DataFrame) -> list[Candle]:
        if frame.empty:
            return []

        candles: list[Candle] = []
        for timestamp, row in frame.iterrows():
            candles.append(
                Candle(
                    open_time=self._coerce_datetime(timestamp),
                    close_time=self._coerce_close_time(timestamp, row),
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]),
                )
            )
        return candles

    def _from_sequence(self, rows: list[Any] | tuple[Any, ...]) -> list[Candle]:
        candles: list[Candle] = []
        for row in rows:
            if isinstance(row, dict):
                timestamp = row.get("timestamp") or row.get("open_time")
                open_price = row["open"]
                high_price = row["high"]
                low_price = row["low"]
                close_price = row["close"]
                volume = row["volume"]
                close_time = row.get("close_time", timestamp)
            else:
                if len(row) < 6:
                    raise ValueError("OHLCV rows must contain at least 6 values")
                timestamp, open_price, high_price, low_price, close_price, volume = row[
                    :6
                ]
                close_time = timestamp

            candles.append(
                Candle(
                    open_time=self._coerce_datetime(timestamp),
                    close_time=self._coerce_datetime(close_time),
                    open=float(open_price),
                    high=float(high_price),
                    low=float(low_price),
                    close=float(close_price),
                    volume=float(volume),
                )
            )
        return candles

    @staticmethod
    def _coerce_close_time(timestamp: Any, row: pd.Series) -> datetime:
        close_time = row.get("close_time")
        if close_time is not None and not pd.isna(close_time):
            return BinanceBootstrapGateway._coerce_datetime(close_time)
        return BinanceBootstrapGateway._coerce_datetime(timestamp)

    @staticmethod
    def _coerce_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value.astimezone(timezone.utc)
        if isinstance(value, pd.Timestamp):
            if value.tzinfo is None:
                return value.to_pydatetime().replace(tzinfo=timezone.utc)
            return value.tz_convert(timezone.utc).to_pydatetime()
        if isinstance(value, (int, float)):
            epoch_value = float(value)
            if abs(epoch_value) >= 10_000_000_000:
                epoch_value /= 1000.0
            return datetime.fromtimestamp(epoch_value, tz=timezone.utc)
        raise TypeError(f"unsupported timestamp value: {value!r}")

    @staticmethod
    def _normalize_client_symbol(symbol: str) -> str:
        text = str(symbol).strip()
        if not text:
            raise ValueError("symbol must not be empty")
        if "/" in text:
            return _SYMBOL_MAPPER.to_canonical_symbol(
                _SYMBOL_MAPPER.to_exchange_symbol(text)
            )
        return _SYMBOL_MAPPER.to_canonical_symbol(text)

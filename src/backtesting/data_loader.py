"""Shared historical-data loading helpers for backtests and hyperopt."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, Optional, Tuple

import numpy as np
import pandas as pd

SUPPORTED_SUFFIXES = (".csv", ".json", ".parquet", ".feather")
TIME_COLUMNS = ("timestamp", "date", "datetime", "time")
REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]


def parse_timerange(
    timerange: str | None,
) -> Tuple[Optional[datetime], Optional[datetime]]:
    """Parse a simple Freqtrade-style YYYYMMDD-YYYYMMDD timerange."""

    if not timerange:
        return None, None

    if "-" not in timerange:
        raise ValueError("Timerange must use the format YYYYMMDD-YYYYMMDD")

    start_raw, end_raw = timerange.split("-", 1)
    start = datetime.strptime(start_raw, "%Y%m%d") if start_raw else None
    end = datetime.strptime(end_raw, "%Y%m%d") if end_raw else None
    return start, end


def symbol_to_filename(symbol: str) -> str:
    return symbol.replace("/", "_").replace(":", "_")


def load_historical_data(
    symbols: Iterable[str],
    timeframe: str = "1h",
    days: int = 365,
    data_dir: str | None = None,
    timerange: str | None = None,
    synthetic_fallback: bool = True,
    random_seed: int = 42,
) -> Dict[str, pd.DataFrame]:
    """Load OHLCV data from disk, falling back to synthetic candles if needed."""

    start_time, end_time = parse_timerange(timerange)
    resolved_days = _resolve_days(days, start_time, end_time)
    data: Dict[str, pd.DataFrame] = {}

    for offset, symbol in enumerate(symbols):
        dataframe = None
        if data_dir:
            dataframe = _load_symbol_from_directory(Path(data_dir), symbol)
            if dataframe is not None:
                dataframe = _filter_by_timerange(dataframe, start_time, end_time)

        if dataframe is None and synthetic_fallback:
            dataframe = generate_synthetic_data(
                symbol,
                timeframe=timeframe,
                days=resolved_days,
                random_seed=random_seed + offset,
                start_time=start_time,
                end_time=end_time,
            )

        if dataframe is not None and not dataframe.empty:
            data[symbol] = dataframe

    return data


def generate_synthetic_data(
    symbol: str,
    timeframe: str,
    days: int,
    random_seed: int = 42,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> pd.DataFrame:
    """Generate deterministic OHLCV data for demos, tests, and fallback hyperopt."""

    if timeframe == "1d":
        periods = max(days, 1)
        freq = "D"
    elif timeframe == "15m":
        periods = max(days * 24 * 4, 1)
        freq = "15min"
    elif timeframe == "5m":
        periods = max(days * 24 * 12, 1)
        freq = "5min"
    else:
        periods = max(days * 24, 1)
        freq = "h"

    if end_time is None:
        end_time = datetime.now()
    if start_time is None:
        start_time = end_time - timedelta(days=days)

    timestamps = pd.date_range(start=start_time, periods=periods, freq=freq)
    rng = np.random.default_rng(random_seed)

    if "BTC" in symbol:
        base_price = 45000.0
        volatility = 0.02
    elif "ETH" in symbol:
        base_price = 3000.0
        volatility = 0.025
    else:
        base_price = 100.0
        volatility = 0.03

    prices = [base_price]
    for _ in range(1, periods + 1):
        drift = 0.0002
        random_shock = rng.normal(0, volatility)
        prices.append(prices[-1] * np.exp(drift + random_shock))

    opens = np.array(prices[:-1])
    closes = np.array(prices[1:])
    highs = np.maximum(opens, closes) * (1 + np.abs(rng.normal(0, 0.01, periods)))
    lows = np.minimum(opens, closes) * (1 - np.abs(rng.normal(0, 0.01, periods)))
    volumes = rng.uniform(1000, 10000, periods)

    return pd.DataFrame(
        {
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": volumes,
        },
        index=timestamps,
    )


def _resolve_days(
    fallback_days: int,
    start_time: datetime | None,
    end_time: datetime | None,
) -> int:
    if start_time and end_time:
        return max((end_time - start_time).days, 1)
    return max(fallback_days, 1)


def _filter_by_timerange(
    dataframe: pd.DataFrame,
    start_time: datetime | None,
    end_time: datetime | None,
) -> pd.DataFrame:
    filtered = dataframe
    if start_time is not None:
        filtered = filtered[filtered.index >= start_time]
    if end_time is not None:
        filtered = filtered[filtered.index <= end_time]
    return filtered.copy()


def _load_symbol_from_directory(data_dir: Path, symbol: str) -> pd.DataFrame | None:
    stem = symbol_to_filename(symbol)
    for suffix in SUPPORTED_SUFFIXES:
        candidate = data_dir / f"{stem}{suffix}"
        if candidate.exists():
            return _normalize_ohlcv(_read_ohlcv_file(candidate))
    return None


def _read_ohlcv_file(path: Path) -> pd.DataFrame:
    if path.suffix == ".csv":
        return pd.read_csv(path)
    if path.suffix == ".json":
        return pd.read_json(path)
    if path.suffix == ".parquet":
        return pd.read_parquet(path)
    if path.suffix == ".feather":
        return pd.read_feather(path)
    raise ValueError(f"Unsupported OHLCV file format: {path.suffix}")


def _normalize_ohlcv(dataframe: pd.DataFrame) -> pd.DataFrame:
    if dataframe.empty:
        return dataframe

    df = dataframe.copy()

    time_column = next(
        (column for column in TIME_COLUMNS if column in df.columns), None
    )
    if time_column:
        df[time_column] = pd.to_datetime(df[time_column], utc=False)
        df.set_index(time_column, inplace=True)
    elif not isinstance(df.index, pd.DatetimeIndex):
        raise ValueError("Historical OHLCV data must include a datetime column")

    missing = [column for column in REQUIRED_COLUMNS if column not in df.columns]
    if missing:
        raise ValueError(f"Historical OHLCV data is missing columns: {missing}")

    normalized = df[REQUIRED_COLUMNS].copy()
    normalized.sort_index(inplace=True)
    return normalized

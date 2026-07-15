"""Market data endpoints for dashboard charting."""

from fastapi import APIRouter, Depends, HTTPException, Query

from api.dependencies import get_bot

router = APIRouter()

ALLOWED_TIMEFRAMES = ("1m", "5m", "15m", "1h", "4h", "1d")
OHLCV_DEFAULT_LIMIT = 300


def _configured_pairs(bot) -> list:
    config = getattr(bot, "config", None)
    if isinstance(config, dict):
        return config.get("trading", {}).get("pairs", []) or []
    return []


@router.get("/ohlcv")
async def get_ohlcv(
    symbol: str = Query(...),
    timeframe: str = Query(default="15m"),
    limit: int = Query(default=OHLCV_DEFAULT_LIMIT, ge=10, le=1000),
    bot=Depends(get_bot),
):
    """Candlestick data for charting.

    Returns Lightweight Charts-ready bars with epoch-second ``time`` keys.
    Only configured trading pairs are served so the public dashboard can't
    be used as an open exchange proxy.
    """
    if timeframe not in ALLOWED_TIMEFRAMES:
        raise HTTPException(
            status_code=422,
            detail=f"timeframe must be one of {list(ALLOWED_TIMEFRAMES)}",
        )

    pairs = _configured_pairs(bot)
    if pairs and symbol not in pairs:
        raise HTTPException(
            status_code=404, detail=f"symbol not in configured pairs: {pairs}"
        )

    exchange = getattr(bot, "data_exchange", None) or getattr(bot, "exchange", None)
    if exchange is None:
        raise HTTPException(status_code=503, detail="Exchange client unavailable")

    try:
        df = await exchange.get_ohlcv(symbol, timeframe, limit=limit)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Failed to fetch OHLCV: {exc}"
        ) from exc

    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "data": [
            {
                # .value is ns since epoch; naive pandas timestamps are UTC here
                "time": int(ts.value // 1_000_000_000),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
            for ts, row in df.iterrows()
        ],
    }

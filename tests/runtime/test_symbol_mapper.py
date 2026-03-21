from __future__ import annotations

import pytest

from adapters.binance.symbol_mapper import BinanceSymbolMapper


def test_symbol_mapper_round_trips_canonical_and_exchange_symbols() -> None:
    mapper = BinanceSymbolMapper()

    assert mapper.to_exchange_symbol("BTC/USDT") == "BTCUSDT"
    assert mapper.to_canonical_symbol("BTCUSDT") == "BTC/USDT"
    assert mapper.to_exchange_symbol("eth/usdt") == "ETHUSDT"
    assert mapper.to_canonical_symbol("ethusdt") == "ETH/USDT"


def test_symbol_mapper_prefers_longest_matching_quote_suffix() -> None:
    mapper = BinanceSymbolMapper(quote_assets=("USD", "USDT"))

    assert mapper.to_canonical_symbol("BTCUSDT") == "BTC/USDT"


def test_symbol_mapper_rejects_invalid_symbols() -> None:
    mapper = BinanceSymbolMapper()

    with pytest.raises(ValueError, match="base/quote form"):
        mapper.to_exchange_symbol("BTCUSDT")

    with pytest.raises(ValueError, match="exchange symbol must contain only"):
        mapper.to_canonical_symbol("BTC/USDT")

    with pytest.raises(ValueError, match="unsupported Binance exchange symbol"):
        mapper.to_canonical_symbol("FOOBAR")

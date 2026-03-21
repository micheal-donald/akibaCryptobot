"""Symbol normalization helpers for Binance market-data adapters."""

from __future__ import annotations

from dataclasses import dataclass

_DEFAULT_QUOTE_ASSETS = (
    "USDT",
    "FDUSD",
    "USDC",
    "BUSD",
    "TUSD",
    "BTC",
    "ETH",
    "BNB",
    "TRY",
    "EUR",
    "GBP",
    "BRL",
    "AUD",
    "DAI",
    "IDRT",
    "NGN",
    "RUB",
    "UAH",
    "ZAR",
    "JPY",
    "MXN",
)


@dataclass(slots=True, frozen=True)
class BinanceSymbolMapper:
    """Convert between canonical config symbols and Binance exchange symbols."""

    quote_assets: tuple[str, ...] = _DEFAULT_QUOTE_ASSETS

    def __post_init__(self) -> None:
        normalized_quotes: list[str] = []
        seen: set[str] = set()
        for quote_asset in self.quote_assets:
            normalized_quote = self._normalize_asset(quote_asset, label="quote asset")
            if normalized_quote not in seen:
                normalized_quotes.append(normalized_quote)
                seen.add(normalized_quote)

        if not normalized_quotes:
            raise ValueError("quote_assets must not be empty")

        object.__setattr__(self, "quote_assets", tuple(normalized_quotes))

    def to_exchange_symbol(self, canonical_symbol: str) -> str:
        """Map a canonical `BASE/QUOTE` symbol to Binance's raw symbol format."""

        base, quote = self._parse_canonical_symbol(canonical_symbol)
        return f"{base}{quote}"

    def to_canonical_symbol(self, exchange_symbol: str) -> str:
        """Map a Binance exchange symbol back to canonical `BASE/QUOTE` form."""

        normalized_symbol = self._normalize_exchange_symbol(exchange_symbol)
        quote = self._match_quote_asset(normalized_symbol)
        base = normalized_symbol[: -len(quote)]
        if not base:
            raise ValueError(
                f"exchange symbol {exchange_symbol!r} does not contain a base asset"
            )
        return f"{base}/{quote}"

    def _parse_canonical_symbol(self, symbol: str) -> tuple[str, str]:
        normalized_symbol = self._normalize_text(symbol)
        if normalized_symbol.count("/") != 1:
            raise ValueError(f"canonical symbol must use base/quote form: {symbol!r}")
        base, quote = normalized_symbol.split("/", 1)
        base = self._normalize_asset(base, label="base asset")
        quote = self._normalize_asset(quote, label="quote asset")
        return base, quote

    @staticmethod
    def _normalize_exchange_symbol(symbol: str) -> str:
        normalized_symbol = BinanceSymbolMapper._normalize_text(symbol)
        if not normalized_symbol or not normalized_symbol.isalnum():
            raise ValueError(
                f"exchange symbol must contain only letters and digits: {symbol!r}"
            )
        return normalized_symbol

    def _match_quote_asset(self, exchange_symbol: str) -> str:
        for quote_asset in sorted(self.quote_assets, key=len, reverse=True):
            if exchange_symbol.endswith(quote_asset) and len(exchange_symbol) > len(
                quote_asset
            ):
                return quote_asset
        raise ValueError(f"unsupported Binance exchange symbol: {exchange_symbol!r}")

    @staticmethod
    def _normalize_text(symbol: str) -> str:
        normalized_symbol = str(symbol).strip().upper()
        if not normalized_symbol:
            raise ValueError("symbol must not be empty")
        return normalized_symbol

    @staticmethod
    def _normalize_asset(asset: str, *, label: str) -> str:
        normalized_asset = BinanceSymbolMapper._normalize_text(asset)
        if not normalized_asset.isalnum():
            raise ValueError(f"{label} must contain only letters and digits: {asset!r}")
        return normalized_asset

"""Per-symbol model registry for ML-based strategies.

Extracted from ``MLFreqAIStrategy`` so any future per-symbol ML strategy
(e.g. XGBoost, PyTorch transformers) can compose the same bookkeeping
instead of reimplementing four parallel dicts. Keeping the storage and
normalization in one place also removes the temptation for
``EnsembleStrategy`` to reach into strategy internals via reflection —
the ensemble now talks to this surface through the strategy's public
``load_model_for_symbol`` / ``symbol_from_model_filename`` methods.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional


@dataclass(frozen=True)
class SymbolModelEntry:
    """Everything a per-symbol inference call needs to resolve."""

    model: Any
    feature_columns: List[str]
    trained_at: Optional[datetime]
    is_expired: bool


class SymbolModelRegistry:
    """Storage for (symbol → trained model, feature columns, expiry state).

    All keys are normalized through :meth:`normalize` so ``BTC/USDT``,
    ``BTC_USDT``, and ``BTCUSDT`` collapse to the same slot. This matches
    the ``<SYMBOL>_freqai.joblib`` filename convention used on disk.
    """

    def __init__(self) -> None:
        self._entries: Dict[str, SymbolModelEntry] = {}

    @staticmethod
    def normalize(symbol: str) -> str:
        """Canonical registry key. Idempotent and side-effect-free."""

        if not symbol:
            return ""
        return symbol.replace("/", "").replace("_", "").upper()

    def register(
        self,
        symbol: str,
        *,
        model: Any,
        feature_columns: Iterable[str],
        trained_at: Optional[datetime],
        is_expired: bool,
    ) -> str:
        """Insert or replace the entry for ``symbol``. Returns the normalized key."""

        key = self.normalize(symbol)
        self._entries[key] = SymbolModelEntry(
            model=model,
            feature_columns=list(feature_columns),
            trained_at=trained_at,
            is_expired=is_expired,
        )
        return key

    def has(self, symbol: str) -> bool:
        return self.normalize(symbol) in self._entries

    def resolve(self, symbol: str) -> Optional[SymbolModelEntry]:
        return self._entries.get(self.normalize(symbol))

    def keys(self) -> List[str]:
        return list(self._entries.keys())

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, symbol: object) -> bool:
        if not isinstance(symbol, str):
            return False
        return self.has(symbol)


__all__ = ["SymbolModelEntry", "SymbolModelRegistry"]

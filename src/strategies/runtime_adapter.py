"""Compatibility adapter for running legacy strategies in the runtime path."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha1
from typing import Callable

import pandas as pd

from domain.events import OrderIntentPayload
from domain.strategy import StrategySnapshot
from strategies.base_strategy import BaseStrategy, SignalType, TradingSignal


QuantityResolver = Callable[[StrategySnapshot, TradingSignal], float]


@dataclass(slots=True)
class LegacyStrategyRuntimeAdapter:
    """Adapt legacy TradingSignal strategies to the runtime intent contract."""

    strategy: BaseStrategy
    entry_quantity: float = 0.0
    order_type: str = "market"
    quantity_resolver: QuantityResolver | None = None

    async def generate_intent(
        self,
        snapshot: StrategySnapshot,
    ) -> OrderIntentPayload | None:
        """Return a runtime intent derived from a legacy strategy signal."""

        current_price = self._resolve_current_price(snapshot)
        if current_price is None or not snapshot.closed_candles:
            return None

        ohlcv_data = self._snapshot_to_dataframe(snapshot)
        if ohlcv_data.empty:
            return None

        if snapshot.position is not None and snapshot.position.open_qty > 0:
            exit_signal = await self.strategy.should_exit_position(
                snapshot.symbol,
                self._position_to_legacy_dict(snapshot),
                current_price,
                ohlcv_data,
            )
            return self._signal_to_intent(snapshot, exit_signal)

        signal = await self.strategy.generate_signal(
            snapshot.symbol,
            ohlcv_data,
            current_price,
        )
        return self._signal_to_intent(snapshot, signal)

    def _signal_to_intent(
        self,
        snapshot: StrategySnapshot,
        signal: TradingSignal | None,
    ) -> OrderIntentPayload | None:
        if signal is None or signal.signal_type is SignalType.HOLD:
            return None

        quantity = self._resolve_quantity(snapshot, signal)
        if quantity is None or quantity <= 0:
            return None

        side, reduce_only = self._resolve_side(snapshot, signal)
        if side is None:
            return None

        reason = signal.metadata.get("exit_reason") or signal.signal_type.value
        candle_close_time = snapshot.closed_candles[-1].close_time
        strategy_name = getattr(self.strategy, "name", type(self.strategy).__name__)
        return OrderIntentPayload(
            intent_id=self._build_intent_id(
                snapshot=snapshot,
                signal=signal,
                candle_close_time=candle_close_time,
                reduce_only=reduce_only,
            ),
            symbol=snapshot.symbol,
            side=side,
            order_type=self.order_type,  # type: ignore[arg-type]
            quantity=quantity,
            stop_loss=signal.stop_loss,
            take_profit=signal.take_profit,
            reduce_only=reduce_only,
            strategy=strategy_name,
            reason=reason,
        )

    def _resolve_quantity(
        self,
        snapshot: StrategySnapshot,
        signal: TradingSignal,
    ) -> float | None:
        if signal.signal_type in {SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT}:
            if snapshot.position is None or snapshot.position.open_qty <= 0:
                return None
            return snapshot.position.open_qty

        if self.quantity_resolver is not None:
            return self.quantity_resolver(snapshot, signal)

        return self.entry_quantity

    @staticmethod
    def _resolve_side(
        snapshot: StrategySnapshot,
        signal: TradingSignal,
    ) -> tuple[str | None, bool]:
        if signal.signal_type is SignalType.BUY:
            return "buy", False
        if signal.signal_type is SignalType.SELL:
            return "sell", False
        if signal.signal_type is SignalType.CLOSE_LONG:
            if snapshot.position is None:
                return None, True
            return "sell", True
        if signal.signal_type is SignalType.CLOSE_SHORT:
            if snapshot.position is None:
                return None, True
            return "buy", True
        return None, False

    @staticmethod
    def _resolve_current_price(snapshot: StrategySnapshot) -> float | None:
        if snapshot.last_price is not None:
            return snapshot.last_price
        if snapshot.closed_candles:
            return snapshot.closed_candles[-1].close
        return None

    @staticmethod
    def _snapshot_to_dataframe(snapshot: StrategySnapshot) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "open_time": candle.open_time,
                    "close_time": candle.close_time,
                    "open": candle.open,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "volume": candle.volume,
                }
                for candle in snapshot.closed_candles
            ]
        )

    @staticmethod
    def _position_to_legacy_dict(snapshot: StrategySnapshot) -> dict[str, object]:
        assert snapshot.position is not None
        return {
            "symbol": snapshot.position.symbol,
            "side": snapshot.position.side,
            "amount": snapshot.position.open_qty,
            "entry_price": snapshot.position.avg_entry_price,
            "stop_loss": snapshot.position.stop_loss,
            "take_profit": snapshot.position.take_profit,
            "trailing_stop": snapshot.position.trailing_stop,
        }

    def _build_intent_id(
        self,
        *,
        snapshot: StrategySnapshot,
        signal: TradingSignal,
        candle_close_time,
        reduce_only: bool,
    ) -> str:
        strategy_name = getattr(self.strategy, "name", type(self.strategy).__name__)
        basis = "|".join(
            (
                strategy_name,
                snapshot.symbol,
                snapshot.timeframe,
                candle_close_time.isoformat(),
                signal.signal_type.value,
                "reduce_only" if reduce_only else "entry",
            )
        )
        return f"intent-{sha1(basis.encode('utf-8')).hexdigest()[:16]}"


__all__ = ["LegacyStrategyRuntimeAdapter", "QuantityResolver"]

import logging
from typing import Dict

from runtime.view_models import ProjectionSource

logger = logging.getLogger(__name__)


class ReconciliationManager:
    """
    Handles comparison between local DB state and actual Exchange balance.
    Detects orphans (assets on exchange but not in DB) and
    lost coins (active trades in DB but no assets on exchange).
    """

    def __init__(self, bot):
        self.bot = bot
        self.ignore_dust_threshold = 0.0001  # Ignore discrepancies smaller than this

    async def reconcile(self) -> Dict:
        """
        Performs the reconciliation check.
        Returns a summary of discrepancies found.
        """
        logger.info("Starting balance reconciliation check...")
        discrepancies = {
            "source": "legacy",
            "orphans": [],  # Assets on exchange but NOT in DB
            "inventory_only": [],
            "inventory_only_count": 0,
            "inventory_only_overflow_count": 0,
            "lost_trades": [],  # Trades in DB but assets NOT on exchange
            "mismatches": [],  # Amount in DB != Amount on exchange
            "runtime_drift": [],
            "runtime_recovery": self._get_runtime_recovery(),
        }

        try:
            # 1. Fetch live balance from exchange
            raw_balance = await self.bot.exchange.get_balance()

            # Filter for assets where total (free + locked) > 0
            live_balances = {}
            # CCXT balance dict contains asset keys + metadata keys ('info', 'free', etc.)
            for asset, data in raw_balance.items():
                if asset in ["info", "free", "used", "total"]:
                    continue
                if isinstance(data, dict):
                    total = data.get("total", data.get("free", 0) + data.get("used", 0))
                    if total > 0:
                        live_balances[asset] = total

            projection = self.bot.get_runtime_projection()
            if projection.source is ProjectionSource.RUNTIME_PROJECTION:
                discrepancies["source"] = projection.source.value
                self._reconcile_runtime_projection(
                    projection=projection,
                    live_balances=live_balances,
                    discrepancies=discrepancies,
                )
            else:
                active_trades = self.bot.active_trades
                self._reconcile_legacy_positions(
                    active_trades=active_trades,
                    live_balances=live_balances,
                    discrepancies=discrepancies,
                )

            # 4. Check for Orphans (Remaining items in live_balances)
            sandbox_inventory_only = (
                discrepancies["source"] == ProjectionSource.RUNTIME_PROJECTION.value
                and self._is_sandbox_mode()
            )
            for asset, amount in live_balances.items():
                if asset in ["USDT", "BNB"]:  # Skip quote and fee tokens
                    continue

                if amount > self.ignore_dust_threshold:
                    entry = {
                        "asset": asset,
                        "amount": amount,
                        "reason": (
                            "Asset found on Exchange with no active "
                            "trade record in the authoritative state source"
                        ),
                    }
                    if sandbox_inventory_only:
                        entry["reason"] = (
                            "Sandbox exchange inventory exists outside the "
                            "runtime-owned state source"
                        )
                        discrepancies["inventory_only_count"] += 1
                        if len(discrepancies["inventory_only"]) < 25:
                            discrepancies["inventory_only"].append(entry)
                        else:
                            discrepancies["inventory_only_overflow_count"] += 1
                    else:
                        discrepancies["orphans"].append(entry)

            self._report_discrepancies(discrepancies)
            return discrepancies

        except Exception as e:
            logger.error(f"Reconciliation failed: {e}")
            return discrepancies

    def _report_discrepancies(self, discrepancies: Dict):
        """Logs and optionally notifies about issues."""
        found_issues = False

        for item in discrepancies["lost_trades"]:
            msg = f"⚠️ LOST TRADE: {item['symbol']} (Expected {item['expected']}, Got {item['actual']})"
            logger.warning(msg)
            self._notify(msg)
            found_issues = True

        for item in discrepancies["orphans"]:
            msg = f"🧐 ORPHAN ASSET: Found {item['amount']} {item['asset']} on exchange with no active DB record."
            logger.warning(msg)
            # Orphans are common (dust), so maybe don't flood Telegram unless large
            found_issues = True

        if discrepancies["inventory_only_count"] > 0:
            sample_count = len(discrepancies["inventory_only"])
            overflow_count = discrepancies["inventory_only_overflow_count"]
            logger.info(
                "ℹ️ SANDBOX INVENTORY: %s exchange-only assets detected outside "
                "runtime state (%s shown, %s omitted).",
                discrepancies["inventory_only_count"],
                sample_count,
                overflow_count,
            )

        for item in discrepancies["mismatches"]:
            msg = f"⚖️ MISMATCH: {item['symbol']} amount mismatch. DB: {item['expected']}, Exchange: {item['actual']}"
            logger.warning(msg)
            found_issues = True

        for item in discrepancies["runtime_drift"]:
            msg = (
                f"🧭 RUNTIME DRIFT: {item['symbol']} "
                f"({item['kind']}) {item['reason']}"
            )
            logger.warning(msg)
            found_issues = True

        if not found_issues:
            logger.info("✅ Reconciliation complete: Local DB matches Exchange state.")

    def _notify(self, message: str):
        """Sends notification via Telegram if enabled."""
        if self.bot.telegram and self.bot.telegram.enabled:
            import asyncio

            task = asyncio.create_task(self.bot.telegram.send_message(message))
            self.bot._background_tasks.add(task)
            task.add_done_callback(self.bot._background_tasks.discard)

    def _reconcile_legacy_positions(
        self,
        *,
        active_trades: Dict[str, Dict],
        live_balances: dict[str, float],
        discrepancies: dict,
    ) -> None:
        for symbol, trade in active_trades.items():
            base_asset = symbol.split("/")[0]
            expected_amount = trade["amount"]
            actual_amount = live_balances.get(base_asset, 0)

            if actual_amount == 0:
                discrepancies["lost_trades"].append(
                    {
                        "symbol": symbol,
                        "expected": expected_amount,
                        "actual": 0,
                        "reason": "Trade exists in DB but asset is missing from Exchange",
                    }
                )
            elif abs(actual_amount - expected_amount) > (
                expected_amount * 0.01
            ):
                discrepancies["mismatches"].append(
                    {
                        "symbol": symbol,
                        "expected": expected_amount,
                        "actual": actual_amount,
                        "diff": actual_amount - expected_amount,
                    }
                )

            if base_asset in live_balances:
                del live_balances[base_asset]

    def _reconcile_runtime_projection(
        self,
        *,
        projection,
        live_balances: dict[str, float],
        discrepancies: dict,
    ) -> None:
        for position in projection.positions:
            if position.open_qty <= 0:
                continue

            base_asset = position.symbol.split("/")[0]
            expected_amount = position.open_qty
            actual_amount = live_balances.get(base_asset, 0)

            if position.status == "reconcile_error":
                discrepancies["runtime_drift"].append(
                    {
                        "symbol": position.symbol,
                        "kind": "reconcile_error",
                        "reason": "position already flagged for manual reconciliation",
                    }
                )

            if actual_amount == 0:
                discrepancies["lost_trades"].append(
                    {
                        "symbol": position.symbol,
                        "expected": expected_amount,
                        "actual": 0,
                        "reason": (
                            "Runtime position exists but asset is missing from Exchange"
                        ),
                    }
                )
            elif abs(actual_amount - expected_amount) > (expected_amount * 0.01):
                discrepancies["mismatches"].append(
                    {
                        "symbol": position.symbol,
                        "expected": expected_amount,
                        "actual": actual_amount,
                        "diff": actual_amount - expected_amount,
                    }
                )

            if base_asset in live_balances:
                del live_balances[base_asset]

        recovery_report = discrepancies.get("runtime_recovery") or {}
        for issue in recovery_report.get("issues", []):
            kind = issue.get("kind")
            if kind == "restored":
                continue
            discrepancies["runtime_drift"].append(
                {
                    "symbol": issue.get("symbol"),
                    "kind": kind,
                    "reason": issue.get("reason"),
                }
            )

    def _get_runtime_recovery(self):
        getter = getattr(self.bot, "get_runtime_recovery_info", None)
        if not callable(getter):
            return None
        return getter()

    def _is_sandbox_mode(self) -> bool:
        config = getattr(self.bot, "config", {})
        if not isinstance(config, dict):
            return True
        exchange = config.get("exchange", {})
        if not isinstance(exchange, dict):
            return True
        return bool(exchange.get("sandbox", True))

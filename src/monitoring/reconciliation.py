import logging
from typing import Dict

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
            "orphans": [],  # Assets on exchange but NOT in DB
            "lost_trades": [],  # Trades in DB but assets NOT on exchange
            "mismatches": [],  # Amount in DB != Amount on exchange
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

            # 2. Get active trades from bot's memory (seeded from DB)
            active_trades = self.bot.active_trades

            # 3. Check for Lost Trades and Mismatches
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
                ):  # 1% tolerance
                    # If discrepancy is significant
                    discrepancies["mismatches"].append(
                        {
                            "symbol": symbol,
                            "expected": expected_amount,
                            "actual": actual_amount,
                            "diff": actual_amount - expected_amount,
                        }
                    )

                # Remove from tracking to find orphans later
                if base_asset in live_balances:
                    del live_balances[base_asset]

            # 4. Check for Orphans (Remaining items in live_balances)
            for asset, amount in live_balances.items():
                if asset in ["USDT", "BNB"]:  # Skip quote and fee tokens
                    continue

                # Check value in USDT if possible
                if amount > self.ignore_dust_threshold:
                    discrepancies["orphans"].append(
                        {
                            "asset": asset,
                            "amount": amount,
                            "reason": "Asset found on Exchange with no active trade record in DB",
                        }
                    )

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

        for item in discrepancies["mismatches"]:
            msg = f"⚖️ MISMATCH: {item['symbol']} amount mismatch. DB: {item['expected']}, Exchange: {item['actual']}"
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

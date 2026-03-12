"""Scans for new trading opportunities and places trades."""

from datetime import datetime


class OpportunityScanner:
    """Finds new trading opportunities, validates conditions, and places trades."""

    OHLCV_CANDLE_LIMIT = 200
    MIN_OHLCV_ROWS = 50
    MIN_TRADE_USDT = 10

    def __init__(self, bot):
        self.bot = bot

    async def scan_opportunities(self):
        """Scan for trading opportunities using the active strategy."""
        try:
            for pair in self.bot.config["trading"]["pairs"]:
                if pair in self.bot.active_trades:
                    continue
                if self._at_max_trades():
                    break
                await self._process_pair(pair)
        except Exception as e:
            self.bot.logger.error(f"Error in scan_opportunities: {e}")

    def _at_max_trades(self) -> bool:
        """Return True and log when the open-trade cap is reached."""
        max_trades = self.bot.config["trading"].get("max_open_trades", 3)
        if len(self.bot.active_trades) >= max_trades:
            self.bot.logger.debug(
                "Maximum open trades reached, skipping new opportunities"
            )
            return True
        return False

    async def _process_pair(self, pair: str):
        """Evaluate a single pair and place a trade if conditions are met."""
        try:
            # Check cooldown — skip pairs that recently hit stop-loss
            if pair in self.bot.pair_cooldowns:
                if datetime.now() < self.bot.pair_cooldowns[pair]:
                    self.bot.logger.debug(
                        f"Skipping {pair} - on cooldown until "
                        f"{self.bot.pair_cooldowns[pair]}"
                    )
                    return
                else:
                    del self.bot.pair_cooldowns[
                        pair
                    ]  # Cooldown expired, allow trading again

            ohlcv_data, current_price = await self._get_market_snapshot(pair)
            if ohlcv_data is None:
                return

            # Check bid-ask spread — skip illiquid pairs
            if not await self._check_spread(pair):
                return

            signal = await self.bot.strategy_manager.generate_signal(
                pair, ohlcv_data, current_price
            )
            if not signal or not self.validate_trading_conditions(pair, signal):
                return

            if not self.bot.advanced_risk.check_correlation_diversification(
                pair, self.bot.active_trades
            ):
                self.bot.logger.info(
                    f"Skipping {pair} - correlation diversification check failed"
                )
                return

            position_size = await self._resolve_position_size(
                pair, signal, current_price
            )
            if position_size > 0:
                await self.place_trade(
                    pair, signal.signal_type.value, position_size, current_price
                )
        except Exception as e:
            self.bot.logger.error(f"Error processing {pair}: {e}")

    async def _get_market_snapshot(self, pair: str):
        """Fetch OHLCV history and current price for a pair.

        Returns (ohlcv_data, current_price) or (None, None) on insufficient data.
        """
        ohlcv_data = await self.bot.data_exchange.get_ohlcv(
            pair,
            self.bot.config["trading"]["timeframes"]["primary"],
            self.OHLCV_CANDLE_LIMIT,
        )
        if ohlcv_data is None or len(ohlcv_data) < self.MIN_OHLCV_ROWS:
            self.bot.logger.warning(f"Insufficient data for {pair}")
            return None, None
        ticker = await self.bot.data_exchange.get_ticker(pair)
        return ohlcv_data, ticker["last"]

    async def _resolve_position_size(
        self, pair: str, signal, current_price: float
    ) -> float:
        """Return the position size in base currency, or 0 if balance is too low."""
        balance = self.bot._cached_balance or await self.bot.exchange.get_balance()
        usdt_balance = balance.get("USDT", {}).get("free", 0)

        if usdt_balance < self.MIN_TRADE_USDT:
            self.bot.logger.warning(f"Insufficient balance: {usdt_balance} USDT")
            return 0

        use_atr = (
            self.bot.config.get("advanced_risk", {}).get("use_atr_sizing", False)
            and pair in self.bot.price_data_cache
        )
        if use_atr:
            sizing_result = self.bot.advanced_risk.calculate_optimal_position_size(
                balance=usdt_balance,
                entry_price=current_price,
                symbol_data=self.bot.price_data_cache[pair],
                symbol=pair,
                signal_strength=signal.strength,
            )
            self.bot.logger.info(
                f"ATR-based position size for {pair}: "
                f"{sizing_result.position_size:.6f} "
                f"(ATR: {sizing_result.atr:.2f}, "
                f"Kelly: {sizing_result.kelly_fraction:.2f})"
            )
            return sizing_result.position_size

        return self.bot.risk_manager.calculate_position_size_with_signal(
            usdt_balance, signal.strength, current_price, signal.stop_loss
        )

    async def _check_spread(self, symbol: str) -> bool:
        """Return True if bid-ask spread is acceptable, False if too wide.

        Fails open: if the order book is unavailable, the trade is allowed.
        """
        try:
            max_spread_pct = self.bot.config.get("risk_management", {}).get(
                "max_spread_percent", 0.5
            )
            orderbook = await self.bot.data_exchange.get_market_depth(symbol, limit=5)

            best_bid = orderbook["bids"][0][0] if orderbook.get("bids") else None
            best_ask = orderbook["asks"][0][0] if orderbook.get("asks") else None

            if best_bid is None or best_ask is None or best_bid <= 0:
                self.bot.logger.warning(
                    f"No orderbook data for {symbol}, skipping spread check"
                )
                return True  # fail open

            spread_pct = ((best_ask - best_bid) / best_bid) * 100

            if spread_pct > max_spread_pct:
                self.bot.logger.info(
                    f"Skipping {symbol}: spread {spread_pct:.3f}% "
                    f"> max {max_spread_pct}%"
                )
                return False

            return True
        except Exception as e:
            self.bot.logger.error(f"Error checking spread for {symbol}: {e}")
            return True  # fail open — don't block trading on order book errors

    def validate_trading_conditions(self, symbol: str, signal) -> bool:
        """Validate if trading conditions are met."""
        try:
            # Check risk management limits
            if not self.bot.risk_manager.can_open_position():
                self.bot.logger.debug(f"Risk manager blocked new position for {symbol}")
                return False

            # Check if signal meets minimum strength
            if signal.strength < self.bot.config.get("strategy_config", {}).get(
                "min_signal_strength", 0.45
            ):
                self.bot.logger.debug(
                    f"Signal strength {signal.strength:.2f} below threshold "
                    f"for {symbol}"
                )
                return False

            return True

        except Exception as e:
            self.bot.logger.error(
                f"Error validating trading conditions for {symbol}: {e}"
            )
            return False

    async def place_trade(
        self, symbol: str, side: str, amount: float, entry_price: float
    ):
        """Place a new trade and record it in all tracking systems."""
        try:
            # Calculate stop-loss and take-profit
            stop_loss = self.bot.risk_manager.calculate_stop_loss(entry_price, side)
            take_profit = self.bot.risk_manager.calculate_take_profit(entry_price, side)

            # Place the order
            order = await self.bot.exchange.place_market_order(
                symbol, side, amount, price=entry_price
            )
            if order.get("status") == "rejected":
                self.bot.logger.warning(
                    f"Order rejected for {symbol}: {order.get('reason')}"
                )
                return

            # Register position with risk manager
            self.bot.risk_manager.add_position(
                symbol=symbol,
                side=side,
                amount=amount,
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                order_id=order["id"],
            )

            # Extract entry fee from order response
            fee_info = order.get("fee", {})
            entry_fee = fee_info.get("cost", 0.0) if fee_info else 0.0

            # Track the trade
            trade_dict = {
                "symbol": symbol,
                "side": side,
                "amount": amount,
                "entry_price": entry_price,
                "stop_loss": stop_loss,
                "take_profit": take_profit,
                "timestamp": datetime.now().isoformat(),
                "order_id": order["id"],
                "entry_fee": entry_fee,
                "exit_fee": 0.0,
            }
            db_id = await self.bot.trade_store.insert_trade(trade_dict)
            trade_dict["db_id"] = db_id
            self.bot.active_trades[symbol] = trade_dict

            # Record in performance tracker
            self.bot.performance_tracker.record_trade_entry(
                symbol=symbol,
                side=side,
                amount=amount,
                entry_price=entry_price,
                strategy=self.bot.strategy_manager.active_strategy or "unknown",
            )

            self.bot.logger.info(f"Placed {side} order for {amount} {symbol}")

            if self.bot.telegram:
                await self.bot.telegram.send_message(
                    f"📈 New {side.upper()} trade\n"
                    f"Symbol: {symbol}\n"
                    f"Amount: {amount}\n"
                    f"Entry: ${entry_price:.4f}\n"
                    f"Stop Loss: ${stop_loss:.4f}\n"
                    f"Take Profit: ${take_profit:.4f}"
                )

        except Exception as e:
            self.bot.logger.error(f"Error placing trade: {e}")

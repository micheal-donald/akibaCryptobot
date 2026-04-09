import os
import inspect
from typing import Any, Dict
import logging
from datetime import datetime
from telegram import Bot, Update
from telegram.ext import Application, CommandHandler, ContextTypes
from runtime.view_models import ProjectionSource
from runtime.rollout import build_rollout_summary

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(self, bot_token: str = None, chat_id: str = None):
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID")

        if not self.bot_token:
            logger.warning("Telegram bot token not provided, notifications disabled")
            self.enabled = False
            return

        if not self.chat_id:
            logger.warning("Telegram chat ID not provided, notifications disabled")
            self.enabled = False
            return

        self.enabled = True
        self.bot = Bot(token=self.bot_token)
        self.application = None

        # Bot state for commands
        self.bot_status = "running"
        self.trade_notifications = True
        self.error_notifications = True

        logger.info("Telegram notifier initialized")

    async def send_message(self, message: str, parse_mode: str = None) -> bool:
        """Send a message to the configured chat"""
        if not self.enabled:
            return False

        try:
            await self.bot.send_message(
                chat_id=self.chat_id, text=message, parse_mode=parse_mode
            )
            logger.debug(f"Telegram message sent: {message[:50]}...")
            return True
        except Exception as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    async def send_trade_notification(self, trade_data: Dict):
        """Send trade-specific notification"""
        if not self.enabled or not self.trade_notifications:
            return

        action = trade_data.get("action", "UNKNOWN")
        symbol = trade_data.get("symbol", "UNKNOWN")
        side = trade_data.get("side", "").upper()
        amount = trade_data.get("amount", 0)
        price = trade_data.get("price", 0)

        if action == "entry":
            side_icon = "🟢⬆️" if side == "BUY" else "🔴⬇️"
            side_label = (
                "LONG" if side == "BUY" else "SHORT" if side == "SELL" else side
            )
            message = (
                f"{side_icon} NEW {side_label} ENTRY\n"
                f"Symbol: {symbol}\n"
                f"Amount: {amount}\n"
                f"Entry Price: ${price:.6f}\n"
                f"Time: {datetime.now().strftime('%H:%M:%S')}"
            )

            if "stop_loss" in trade_data:
                message += f"\nStop Loss: ${trade_data['stop_loss']:.6f}"
            if "take_profit" in trade_data:
                message += f"\nTake Profit: ${trade_data['take_profit']:.6f}"

        elif action == "exit":
            pnl = trade_data.get("pnl", 0)
            reason = trade_data.get("reason", "manual")
            pnl_emoji = "✅📈" if pnl > 0 else "🛑📉"

            message = (
                f"{pnl_emoji} CLOSED {side or 'UNKNOWN'} TRADE\n"
                f"Symbol: {symbol}\n"
                f"Exit Price: ${price:.6f}\n"
                f"PnL: ${pnl:.6f}\n"
                f"Reason: {reason}\n"
                f"Time: {datetime.now().strftime('%H:%M:%S')}"
            )

        else:
            message = f"🔄 Trade Update: {symbol} - {action}"

        await self.send_message(message)

    async def send_training_notification(self, training_data: Any):
        """Send a training-job notification or operator summary."""
        if not self.enabled:
            return

        status = self._job_value(training_data, "status", "unknown")
        status_icon_map = {
            "queued": "🧠⏳",
            "running": "🧠⚙️",
            "succeeded": "🧠✅",
            "failed": "🧠❌",
            "blocked": "🧠⛔",
            "idle": "🧠",
        }
        icon = status_icon_map.get(str(status), "🧠")
        job_id = self._job_value(training_data, "job_id", "unknown")
        requested_by = self._job_value(training_data, "requested_by", "unknown")
        pairs = self._job_value(training_data, "pairs", ())
        timeframe = self._job_value(training_data, "timeframe", "unknown")
        candles = self._job_value(training_data, "candles", "unknown")
        progress = self._job_value(training_data, "progress", None)
        result_summary = self._job_value(training_data, "result_summary", None)
        error = self._job_value(training_data, "error", None)

        message = (
            f"{icon} TRAINING JOB {str(status).upper()}\n"
            f"Job ID: {job_id}\n"
            f"Requested By: {requested_by}\n"
            f"Pairs: {', '.join(pairs) if pairs else 'default config'}\n"
            f"Timeframe: {timeframe}\n"
            f"Candles: {candles}"
        )

        if progress is not None:
            message += f"\nProgress: {float(progress):.0f}%"
        if result_summary:
            message += f"\nResult: {result_summary}"
        if error:
            message += f"\nError: {error}"

        await self.send_message(message)

    async def send_error_notification(self, error: str, context: str = ""):
        """Send error notification"""
        if not self.enabled or not self.error_notifications:
            return

        message = (
            f"🚨 ERROR ALERT\n"
            f"Context: {context}\n"
            f"Error: {error}\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

        await self.send_message(message)

    async def send_daily_report(self, report_data: Dict):
        """Send daily performance report"""
        if not self.enabled:
            return

        total_trades = report_data.get("total_trades", 0)
        winning_trades = report_data.get("winning_trades", 0)
        total_pnl = report_data.get("total_pnl", 0)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0

        pnl_emoji = "📈" if total_pnl > 0 else "📉" if total_pnl < 0 else "➖"

        message = (
            f"📊 DAILY REPORT\n"
            f"Date: {datetime.now().strftime('%Y-%m-%d')}\n"
            f"Total Trades: {total_trades}\n"
            f"Winning Trades: {winning_trades}\n"
            f"Win Rate: {win_rate:.1f}%\n"
            f"{pnl_emoji} Total PnL: ${total_pnl:.6f}\n"
        )

        if "balance" in report_data:
            message += f"Current Balance: ${report_data['balance']:.2f}\n"

        if "open_positions" in report_data:
            message += f"Open Positions: {report_data['open_positions']}"

        await self.send_message(message)

    def setup_bot_commands(self, bot_instance):
        """Setup bot command handlers"""
        self.bot_instance = bot_instance

        application = Application.builder().token(self.bot_token).build()

        # Add command handlers
        application.add_handler(CommandHandler("start", self.cmd_start))
        application.add_handler(CommandHandler("status", self.cmd_status))
        application.add_handler(CommandHandler("balance", self.cmd_balance))
        application.add_handler(CommandHandler("trades", self.cmd_trades))
        application.add_handler(CommandHandler("stop", self.cmd_stop))
        application.add_handler(CommandHandler("resume", self.cmd_resume))
        application.add_handler(CommandHandler("help", self.cmd_help))
        application.add_handler(CommandHandler("config", self.cmd_config))
        application.add_handler(CommandHandler("model", self.cmd_model))
        application.add_handler(CommandHandler("train_model", self.cmd_train_model))
        application.add_handler(CommandHandler("train_status", self.cmd_train_status))
        application.add_handler(CommandHandler("performance", self.cmd_performance))
        application.add_handler(CommandHandler("metrics", self.cmd_metrics))
        application.add_handler(CommandHandler("report", self.cmd_report))
        application.add_handler(CommandHandler("risk", self.cmd_risk))
        application.add_handler(CommandHandler("correlations", self.cmd_correlations))
        application.add_handler(CommandHandler("heatmap", self.cmd_heatmap))
        application.add_handler(CommandHandler("reconcile", self.cmd_reconcile))

        self.application = application

        return application

    def _get_runtime_projection(self):
        """Return the current runtime projection for operator-facing commands."""
        if not hasattr(self, "bot_instance"):
            return None

        projection_getter = getattr(self.bot_instance, "get_runtime_projection", None)
        if projection_getter is None:
            return None

        try:
            return projection_getter()
        except Exception as exc:
            logger.error("Failed to load runtime projection for Telegram: %s", exc)
            return None

    @staticmethod
    def _projection_source_value(projection: Any) -> str:
        if projection is None:
            return ProjectionSource.LEGACY.value
        source = getattr(projection, "source", None)
        return getattr(source, "value", str(source or ProjectionSource.LEGACY.value))

    @classmethod
    def _uses_runtime_projection(cls, projection: Any) -> bool:
        return (
            cls._projection_source_value(projection)
            == ProjectionSource.RUNTIME_PROJECTION.value
        )

    @classmethod
    def _runtime_summary_lines(cls, projection: Any) -> list[str]:
        if projection is None:
            return []

        return [
            f"Runtime Mode: {getattr(projection, 'runtime_mode', 'legacy')}",
            f"State Source: {cls._projection_source_value(projection)}",
            f"Runtime Running: {'yes' if getattr(projection, 'runtime_is_running', False) else 'no'}",
            f"Runtime Healthy: {'yes' if getattr(projection, 'runtime_is_healthy', True) else 'no'}",
            f"Runtime Start Status: {getattr(projection, 'runtime_start_status', 'disabled')}",
            f"Runtime Start Pending: {getattr(projection, 'runtime_start_pending', False)}",
            f"Runtime Owns Entries: {getattr(projection, 'runtime_owns_entries', False)}",
            f"Runtime Owns Exits: {getattr(projection, 'runtime_owns_exits', False)}",
        ]

    @classmethod
    def _rollout_summary_lines(
        cls,
        projection: Any,
        *,
        configured_deployment_profile: str | None = None,
        trading_mode: str | None = None,
    ) -> list[str]:
        if projection is None:
            return []
        rollout = build_rollout_summary(
            projection,
            configured_deployment_profile=configured_deployment_profile,
            trading_mode=trading_mode,
        )

        lines = []
        if configured_deployment_profile:
            lines.append(f"Deployment Profile: {configured_deployment_profile}")

        lines.extend(
            [
                f"Rollout Environment: {rollout['rollout_environment']}",
                f"Rollout Stage: {rollout['rollout_stage']}",
                f"Rollout Health: {rollout['rollout_health']}",
                f"Rollout Recommended Action: {rollout['rollout_recommended_action']}",
                f"Rollout Summary: {rollout['rollout_summary']}",
            ]
        )
        return lines

    def _get_runtime_visibility_info(self) -> Dict[str, Any]:
        if not hasattr(self, "bot_instance"):
            return {}

        visibility_getter = getattr(
            self.bot_instance, "get_runtime_visibility_info", None
        )
        if not callable(visibility_getter):
            return {}

        try:
            visibility = visibility_getter()
        except Exception as exc:
            logger.error("Failed to load runtime visibility for Telegram: %s", exc)
            return {}

        if isinstance(visibility, dict):
            return visibility
        return {}

    @staticmethod
    def _job_value(job: Any, key: str, default: Any = None) -> Any:
        if job is None:
            return default
        if isinstance(job, dict):
            return job.get(key, default)
        return getattr(job, key, default)

    async def _call_optional_helper(
        self,
        *helper_names: str,
        default: Any = None,
        **kwargs,
    ) -> Any:
        bot = getattr(self, "bot_instance", None)
        if bot is None:
            return default

        for helper_name in helper_names:
            helper = getattr(bot, helper_name, None)
            if not callable(helper):
                continue
            try:
                result = helper(**kwargs)
                if inspect.isawaitable(result):
                    result = await result
                return result
            except Exception as exc:
                logger.error(
                    "Telegram helper %s failed: %s", helper_name, exc, exc_info=True
                )
                return default
        return default

    def _extract_training_request(
        self, context: ContextTypes.DEFAULT_TYPE
    ) -> Dict[str, Any]:
        args = list(getattr(context, "args", []) or [])
        parsed: Dict[str, Any] = {}
        positional_pairs: list[str] = []

        for arg in args:
            if "=" in arg:
                key, value = arg.split("=", 1)
                key = key.lstrip("-").strip().lower()
                value = value.strip()
                if key in {"pairs", "pair"}:
                    parsed["pairs"] = [
                        item.strip() for item in value.split(",") if item.strip()
                    ]
                elif key in {"candles", "candle_limit"}:
                    parsed["candles"] = int(value)
                elif key == "timeframe":
                    parsed["timeframe"] = value
                elif key in {"by", "requested_by"}:
                    parsed["requested_by"] = value
            else:
                positional_pairs.append(arg)

        if positional_pairs and "pairs" not in parsed:
            parsed["pairs"] = positional_pairs
        return parsed

    @staticmethod
    def _format_model_status_lines(model_status: Dict[str, Any]) -> list[str]:
        if not model_status or not model_status.get("freqai_active"):
            return [
                "🧠 Model Status",
                f"Reason: {model_status.get('reason', 'FreqAI not active')}",
            ]

        lines = ["🧠 Model Status"]
        for model in model_status.get("models", []):
            name = model.get("name", "unknown")
            trained = "yes" if model.get("trained") else "no"
            feature_count = model.get("feature_count", "—")
            stale = "yes" if model.get("stale") else "no"
            age_hours = model.get("age_hours")
            age_text = f"{age_hours:.1f}h ago" if age_hours is not None else "unknown"
            lines.extend(
                [
                    f"Model: {name}",
                    f"Trained: {trained}",
                    f"Feature Count: {feature_count}",
                    f"Age: {age_text}",
                    f"Stale: {stale}",
                ]
            )
            top_features = model.get("top_features") or []
            if top_features:
                feature_text = ", ".join(
                    f"{feature['name']} ({feature['importance']:.3f})"
                    for feature in top_features[:5]
                )
                lines.append(f"Top Features: {feature_text}")
        return lines

    @staticmethod
    def _format_training_job_lines(
        job: Any, heading: str = "🧠 Training Job"
    ) -> list[str]:
        if job is None:
            return [heading, "No training job has been recorded."]

        status = str(TelegramNotifier._job_value(job, "status", "unknown"))
        status_icon_map = {
            "queued": "🧠⏳",
            "running": "🧠⚙️",
            "succeeded": "🧠✅",
            "failed": "🧠❌",
            "blocked": "🧠⛔",
            "idle": "🧠",
        }
        icon = status_icon_map.get(status, "🧠")
        pairs = TelegramNotifier._job_value(job, "pairs", ())
        lines = [
            f"{heading} {icon}",
            f"Job ID: {TelegramNotifier._job_value(job, 'job_id', 'unknown')}",
            f"Status: {status}",
            f"Requested By: {TelegramNotifier._job_value(job, 'requested_by', 'unknown')}",
            f"Pairs: {', '.join(pairs) if pairs else 'default config'}",
            f"Timeframe: {TelegramNotifier._job_value(job, 'timeframe', 'unknown')}",
            f"Candles: {TelegramNotifier._job_value(job, 'candles', 'unknown')}",
        ]
        progress = TelegramNotifier._job_value(job, "progress", None)
        if progress is not None:
            lines.append(f"Progress: {float(progress):.0f}%")
        result_summary = TelegramNotifier._job_value(job, "result_summary", None)
        if result_summary:
            lines.append(f"Result: {result_summary}")
        error = TelegramNotifier._job_value(job, "error", None)
        if error:
            lines.append(f"Error: {error}")
        reload_status = TelegramNotifier._job_value(job, "reload_status", None)
        if reload_status:
            lines.append(f"Reload Status: {reload_status}")
        return lines

    def _get_training_store(self):
        bot = getattr(self, "bot_instance", None)
        if bot is None:
            return None
        store = getattr(bot, "trade_store", None)
        if store is None:
            return None
        if not any(
            hasattr(store, attr)
            for attr in ("get_training_job_status", "enqueue_training_job")
        ):
            return None
        return store

    async def _get_training_job_status(self):
        result = await self._call_optional_helper("get_training_job_status")
        if result is not None:
            return result
        store = self._get_training_store()
        if store is None:
            return None
        helper = getattr(store, "get_training_job_status", None)
        if not callable(helper):
            return None
        result = helper()
        if inspect.isawaitable(result):
            result = await result
        return result

    async def _get_live_rollout_approval_status(self):
        return await self._call_optional_helper("get_live_rollout_approval_status")

    async def _enqueue_training_job(self, request: Dict[str, Any]):
        result = await self._call_optional_helper(
            "enqueue_training_job",
            request=request,
        )
        if result is not None:
            return result
        store = self._get_training_store()
        if store is None:
            return None
        helper = getattr(store, "enqueue_training_job", None)
        if not callable(helper):
            return None
        result = helper(request)
        if inspect.isawaitable(result):
            result = await result
        return result

    def _resolve_training_mode(self) -> str:
        if hasattr(self, "bot_instance"):
            config = getattr(self.bot_instance, "config", None)
            exchange = config.get("exchange", {}) if isinstance(config, dict) else {}
            return "paper" if exchange.get("sandbox", True) else "live"
        return "paper"

    def _resolve_default_pairs(self) -> list[str]:
        bot = getattr(self, "bot_instance", None)
        if bot is None:
            return []
        config = getattr(bot, "config", None)
        trading = config.get("trading", {}) if isinstance(config, dict) else {}
        pairs = trading.get("pairs", [])
        return list(pairs) if isinstance(pairs, (list, tuple)) else []

    def _resolve_default_candles(self) -> int:
        bot = getattr(self, "bot_instance", None)
        if bot is None:
            return 1000
        config = getattr(bot, "config", None)
        freqai = config.get("freqai", {}) if isinstance(config, dict) else {}
        ml_params = freqai.get("ml_params", {}) if isinstance(freqai, dict) else {}
        return int(ml_params.get("training_data_period", 1000))

    def _resolve_default_timeframe(self) -> str:
        bot = getattr(self, "bot_instance", None)
        if bot is None:
            return "1h"
        config = getattr(bot, "config", None)
        trading = config.get("trading", {}) if isinstance(config, dict) else {}
        timeframes = trading.get("timeframes", {}) if isinstance(trading, dict) else {}
        if isinstance(timeframes, dict):
            return str(timeframes.get("primary", "1h"))
        return "1h"

    def _get_model_status_snapshot(self) -> Dict[str, Any]:
        bot = getattr(self, "bot_instance", None)
        if bot is None:
            return {"freqai_active": False, "reason": "Bot instance not available"}

        helper = getattr(bot, "get_model_status_info", None)
        if callable(helper):
            try:
                result = helper()
                if inspect.isawaitable(result):
                    return {"freqai_active": False, "reason": "Model snapshot pending"}
                if isinstance(result, dict):
                    return result
            except Exception:
                logger.exception("Failed to load model status snapshot from bot helper")

        strategy_manager = getattr(bot, "strategy_manager", None)
        if strategy_manager is None:
            return {"freqai_active": False, "reason": "Strategy manager not available"}

        active_strategy_getter = getattr(strategy_manager, "get_active_strategy", None)
        active_strategy = (
            active_strategy_getter() if callable(active_strategy_getter) else None
        )

        if not hasattr(active_strategy, "strategies"):
            return {
                "freqai_active": False,
                "reason": "Active strategy is not an ensemble",
            }

        models = []
        for strategy, weight, name in active_strategy.strategies:
            if not hasattr(strategy, "model_trained"):
                continue

            model_info = {
                "name": name,
                "type": type(strategy).__name__,
                "weight": weight,
                "trained": getattr(strategy, "model_trained", False),
                "feature_count": len(getattr(strategy, "feature_columns", [])),
            }

            models_dir = "models"
            if os.path.exists(models_dir):
                model_files = [
                    f for f in os.listdir(models_dir) if f.endswith("_freqai.joblib")
                ]
                if model_files:
                    newest = max(
                        model_files,
                        key=lambda f: os.path.getmtime(os.path.join(models_dir, f)),
                    )
                    mtime = os.path.getmtime(os.path.join(models_dir, newest))
                    model_info["last_trained"] = datetime.fromtimestamp(
                        mtime
                    ).isoformat()
                    age_hours = (datetime.now().timestamp() - mtime) / 3600
                    model_info["age_hours"] = round(age_hours, 1)
                    model_info["stale"] = age_hours > 48

            if hasattr(strategy, "ml_model") and strategy.ml_model is not None:
                if hasattr(strategy.ml_model, "feature_importances_"):
                    cols = getattr(strategy, "feature_columns", [])
                    importances = strategy.ml_model.feature_importances_
                    if len(cols) == len(importances):
                        top = sorted(
                            zip(cols, importances), key=lambda x: x[1], reverse=True
                        )[:5]
                        model_info["top_features"] = [
                            {"name": n, "importance": round(float(v), 4)}
                            for n, v in top
                        ]

            models.append(model_info)

        any_trained = any(m["trained"] for m in models)
        return {"freqai_active": any_trained, "models": models}

    def _resolve_trading_mode(self, status_info: Dict[str, Any] | None = None) -> str:
        if isinstance(status_info, dict) and status_info.get("trading_mode"):
            return str(status_info["trading_mode"])
        if hasattr(self, "bot_instance"):
            config = getattr(self.bot_instance, "config", None)
            exchange = config.get("exchange", {}) if isinstance(config, dict) else {}
            return "paper" if exchange.get("sandbox", True) else "live"
        return "paper"

    async def _get_operational_alert_state(self) -> Dict[str, Any] | None:
        result = await self._call_optional_helper("get_operational_alert_state")
        if isinstance(result, dict):
            return result

        bot = getattr(self, "bot_instance", None)
        if bot is None:
            return None

        if all(hasattr(bot, attr) for attr in ("operational_alerts", "halt_state")):
            alerts = [
                alert.to_dict() if hasattr(alert, "to_dict") else alert
                for alert in getattr(bot, "operational_alerts", ())
            ]
            return {
                "operational_alerts": alerts,
                "halt_state": getattr(bot, "halt_state", "none"),
                "entries_blocked_reason": getattr(bot, "entries_blocked_reason", None),
            }
        return None

    @staticmethod
    def _critical_alerts(alert_state: Dict[str, Any] | None) -> list[Dict[str, Any]]:
        if not isinstance(alert_state, dict):
            return []
        alerts = alert_state.get("operational_alerts")
        if not isinstance(alerts, list):
            return []
        return [
            alert
            for alert in alerts
            if isinstance(alert, dict) and alert.get("severity") == "critical"
        ]

    def _format_operational_alert_lines(
        self, alert_state: Dict[str, Any] | None
    ) -> list[str]:
        if not isinstance(alert_state, dict):
            return []

        halt_state = str(alert_state.get("halt_state", "none"))
        entries_blocked_reason = alert_state.get("entries_blocked_reason")
        alerts = alert_state.get("operational_alerts")
        if not isinstance(alerts, list):
            alerts = []

        lines = [
            "Operational Alerts",
            f"Halt State: {halt_state}",
        ]
        if entries_blocked_reason:
            lines.append(f"Entries Blocked Reason: {entries_blocked_reason}")
        if not alerts:
            lines.append("Alerts: none")
            return lines

        lines.append(f"Alerts: {len(alerts)} active")
        for alert in alerts[:3]:
            if not isinstance(alert, dict):
                continue
            lines.append(
                f"- {alert.get('severity', 'info')} {alert.get('code', 'unknown')}: "
                f"{alert.get('recommended_action', 'inspect')}"
            )
        if len(alerts) > 3:
            lines.append(f"- plus {len(alerts) - 3} more alert(s)")
        return lines

    @staticmethod
    def _format_live_rollout_lines(
        live_rollout_status: Dict[str, Any] | None,
    ) -> list[str]:
        if not isinstance(live_rollout_status, dict):
            return []

        lines = [
            "Live Rollout Approval",
            f"Approval Required: {live_rollout_status.get('approval_required', False)}",
        ]
        target_profile = live_rollout_status.get("target_profile")
        if target_profile:
            lines.append(f"Target Profile: {target_profile}")

        current_approval = live_rollout_status.get("current_approval")
        if isinstance(current_approval, dict):
            lines.extend(
                [
                    f"Approval Status: {current_approval.get('status', 'unknown')}",
                    f"Approved By: {current_approval.get('actor', 'unknown')}",
                    f"Approval Reason: {current_approval.get('reason', 'n/a')}",
                ]
            )
            expires_at = current_approval.get("expires_at")
            if expires_at:
                lines.append(f"Approval Expires: {expires_at}")
        else:
            lines.append("Approval Status: missing")

        latest_event = live_rollout_status.get("latest_event")
        if isinstance(latest_event, dict):
            lines.append(
                "Last Rollout Event: "
                f"{latest_event.get('event_type', 'unknown')} "
                f"({latest_event.get('target_profile', 'unknown')})"
            )
            lines.append(f"Event Reason: {latest_event.get('reason', 'n/a')}")
        return lines

    async def send_operational_alert_transition(
        self,
        previous_state: Dict[str, Any] | None,
        current_state: Dict[str, Any] | None,
    ) -> None:
        if not self.enabled:
            return

        previous_critical = {
            alert.get("code")
            for alert in self._critical_alerts(previous_state)
            if isinstance(alert, dict)
        }
        current_critical = {
            alert.get("code")
            for alert in self._critical_alerts(current_state)
            if isinstance(alert, dict)
        }
        new_critical = sorted(
            code for code in current_critical - previous_critical if code
        )
        cleared = sorted(code for code in previous_critical - current_critical if code)

        current_halt = (
            current_state.get("halt_state")
            if isinstance(current_state, dict)
            else "none"
        )
        current_reason = (
            current_state.get("entries_blocked_reason")
            if isinstance(current_state, dict)
            else None
        )
        previous_halt = (
            previous_state.get("halt_state")
            if isinstance(previous_state, dict)
            else "none"
        )

        if new_critical:
            lines = [
                "🚨 OPERATIONS ALERT",
                f"Critical Alerts: {', '.join(new_critical)}",
                f"Halt State: {current_halt}",
            ]
            if current_reason:
                lines.append(f"Entries Blocked Reason: {current_reason}")
            await self.send_message("\n".join(lines))
            return

        if previous_halt != "none" and current_halt == "none":
            lines = [
                "✅ OPERATIONS RECOVERY",
                "Critical alerts cleared and entry blocking has been released.",
            ]
            if cleared:
                lines.append(f"Recovered Alerts: {', '.join(cleared)}")
            await self.send_message("\n".join(lines))

    @classmethod
    def _rollout_summary_for_visibility(
        cls,
        projection: Any,
        *,
        visibility: Dict[str, Any] | None = None,
        trading_mode: str | None = None,
    ) -> list[str]:
        visibility = visibility or {}
        return cls._rollout_summary_lines(
            projection,
            configured_deployment_profile=visibility.get(
                "configured_deployment_profile"
            ),
            trading_mode=trading_mode,
        )

    @staticmethod
    def _format_trade_lines(trades_info: Any) -> list[str]:
        if isinstance(trades_info, dict):
            entries = trades_info.items()
        else:
            entries = ((trade.get("symbol", "UNKNOWN"), trade) for trade in trades_info)

        lines = []
        for symbol, trade in entries:
            side = str(trade.get("side", "")).upper()
            amount = trade.get("amount", 0)
            entry = trade.get("entry_price")
            status = trade.get("status")
            entry_text = f"${entry:.6f}" if entry is not None else "n/a"
            status_suffix = f" [{status}]" if status else ""
            lines.append(f"{symbol}: {side} {amount} @ {entry_text}{status_suffix}")
        return lines

    @staticmethod
    def _format_projected_trade_lines(projection: Any) -> list[str]:
        lines = []
        for position in getattr(projection, "positions", ()):
            side = str(getattr(position, "side", "")).upper()
            amount = getattr(position, "open_qty", 0)
            entry = getattr(position, "avg_entry_price", None)
            entry_text = f"${entry:.6f}" if entry is not None else "n/a"
            status = getattr(position, "status", "unknown")
            lines.append(
                f"{position.symbol}: {side} {amount} @ {entry_text} [{status}]"
            )
        return lines

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /start command"""
        welcome_message = (
            "🤖 AkibaCryptoBot activated!\n\n"
            "Available commands:\n"
            "/status - Bot status\n"
            "/balance - Account balance\n"
            "/trades - Active trades\n"
            "/model - FreqAI model health\n"
            "/train_model - Queue model training\n"
            "/train_status - Current model training job\n"
            "/performance - Performance summary\n"
            "/metrics - Detailed metrics\n"
            "/report - Full performance report\n"
            "/risk - Risk management report\n"
            "/correlations - Asset correlations\n"
            "/heatmap - Portfolio risk heatmap\n"
            "/stop - Stop trading\n"
            "/resume - Resume trading\n"
            "/reconcile - Manual balance audit\n"
            "/help - Show this help\n"
            "/config - Bot configuration"
        )
        await update.message.reply_text(welcome_message)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /status command"""
        if hasattr(self, "bot_instance"):
            try:
                status_info = self.bot_instance.get_status_info()
                projection = self._get_runtime_projection()
                active_trades = status_info.get("active_trades", 0)
                if self._uses_runtime_projection(projection):
                    active_trades = getattr(projection, "active_position_count", 0)

                lines = [
                    f"Bot Status: {self.bot_status}",
                    f"Active Trades: {active_trades}",
                    f"Daily PnL: ${status_info.get('daily_pnl', 0):.6f}",
                    f"Uptime: {status_info.get('uptime', 'Unknown')}",
                ]

                if projection is not None:
                    visibility = self._get_runtime_visibility_info()
                    lines.extend(self._runtime_summary_lines(projection))
                    lines.extend(
                        self._rollout_summary_for_visibility(
                            projection,
                            visibility=visibility,
                            trading_mode=self._resolve_trading_mode(status_info),
                        )
                    )
                    lines.append(
                        f"Projected Open Orders: {getattr(projection, 'open_order_count', 0)}"
                    )
                    lines.append(
                        f"Projected Pending Exits: {getattr(projection, 'pending_exit_count', 0)}"
                    )

                training_status = await self._get_training_job_status()
                if training_status is not None:
                    current_job = self._job_value(training_status, "current_job", None)
                    last_job = self._job_value(training_status, "last_job", None)
                    if current_job is not None:
                        lines.extend(
                            self._format_training_job_lines(
                                current_job, heading="Training Job"
                            )
                        )
                    elif last_job is not None:
                        lines.extend(
                            self._format_training_job_lines(
                                last_job, heading="Last Training Job"
                            )
                        )

                alert_state = await self._get_operational_alert_state()
                lines.extend(self._format_operational_alert_lines(alert_state))
                live_rollout_status = await self._get_live_rollout_approval_status()
                lines.extend(self._format_live_rollout_lines(live_rollout_status))

                message = "\n".join(lines)
            except Exception as e:
                message = f"Error fetching status: {str(e)}"
        else:
            message = f"Bot Status: {self.bot_status}\nNo detailed info available"

        await update.message.reply_text(message)

    async def cmd_balance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /balance command"""
        if hasattr(self, "bot_instance"):
            try:
                balance_info = await self.bot_instance.get_balance_info()
                message = "💰 Account Balance:\n"
                for currency, amount in balance_info.items():
                    if amount > 0:
                        message += f"{currency}: {amount:.6f}\n"
            except Exception as e:
                message = f"❌ Error fetching balance: {str(e)}"
        else:
            message = "❌ Bot instance not available"

        await update.message.reply_text(message)

    async def cmd_trades(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /trades command"""
        if hasattr(self, "bot_instance"):
            try:
                projection = self._get_runtime_projection()
                visibility = self._get_runtime_visibility_info()
                trading_mode = self._resolve_trading_mode()
                if self._uses_runtime_projection(projection):
                    trade_lines = self._format_projected_trade_lines(projection)
                    if not trade_lines:
                        lines = ["No active trades"]
                    else:
                        lines = ["Active Trades:", *trade_lines]
                    lines.extend(self._runtime_summary_lines(projection))
                    lines.extend(
                        self._rollout_summary_for_visibility(
                            projection,
                            visibility=visibility,
                            trading_mode=trading_mode,
                        )
                    )
                    lines.append(
                        f"Projected Open Orders: {getattr(projection, 'open_order_count', 0)}"
                    )
                    message = "\n".join(lines)
                else:
                    trades_info = self.bot_instance.get_active_trades()
                    if not trades_info:
                        lines = ["No active trades"]
                    else:
                        lines = [
                            "Active Trades:",
                            *self._format_trade_lines(trades_info),
                        ]

                    if (
                        self._projection_source_value(projection)
                        == ProjectionSource.SHADOW.value
                    ):
                        lines.append(
                            f"Shadow Projected Positions: {getattr(projection, 'active_position_count', 0)}"
                        )
                        lines.append(
                            f"Shadow Open Orders: {getattr(projection, 'open_order_count', 0)}"
                        )
                        lines.extend(self._runtime_summary_lines(projection))
                        lines.extend(
                            self._rollout_summary_for_visibility(
                                projection,
                                visibility=visibility,
                                trading_mode=trading_mode,
                            )
                        )

                    training_status = await self._get_training_job_status()
                    if training_status is not None:
                        current_job = self._job_value(
                            training_status, "current_job", None
                        )
                        if current_job is not None:
                            lines.extend(
                                self._format_training_job_lines(
                                    current_job, heading="Training Job"
                                )
                            )

                    message = "\n".join(lines)
            except Exception as e:
                message = f"Error fetching trades: {str(e)}"
        else:
            message = "Bot instance not available"

        await update.message.reply_text(message)

    async def cmd_stop(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /stop command"""
        self.bot_status = "stopped"
        if hasattr(self, "bot_instance"):
            self.bot_instance.pause_trading()

        await update.message.reply_text("🛑 Trading stopped")

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /resume command"""
        self.bot_status = "running"
        if hasattr(self, "bot_instance"):
            self.bot_instance.resume_trading()

        await update.message.reply_text("▶️ Trading resumed")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /help command"""
        help_message = (
            "🤖 AkibaCryptoBot Commands:\n\n"
            "📊 Information:\n"
            "/status - Bot status and performance\n"
            "/balance - Account balance\n"
            "/trades - List active trades\n"
            "/model - FreqAI model health\n"
            "/train_status - Current training job\n"
            "/performance - Quick performance summary\n"
            "/metrics - Detailed performance metrics\n"
            "/report - Full formatted report\n\n"
            "🎮 Control:\n"
            "/stop - Pause trading\n"
            "/resume - Resume trading\n\n"
            "⚙️ Configuration:\n"
            "/config - Show bot configuration\n\n"
            "🛠 Tools:\n"
            "/train_model - Queue a model training job\n"
            "/reconcile - Audit DB vs Exchange\n\n"
            "❓ Help:\n"
            "/help - Show this help message\n"
            "/start - Restart bot welcome"
        )
        await update.message.reply_text(help_message)

    async def cmd_config(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /config command"""
        if hasattr(self, "bot_instance"):
            try:
                config_info = self.bot_instance.get_config_info()
                message = (
                    "⚙️ Bot Configuration:\n"
                    f"Trading Mode: {config_info.get('trading_mode', 'Unknown')}\n"
                    f"Max Position Size: {config_info.get('max_position_size', 0) * 100:.1f}%\n"
                    f"Stop Loss: {config_info.get('stop_loss_percent', 0) * 100:.1f}%\n"
                    f"Take Profit: {config_info.get('take_profit_percent', 0) * 100:.1f}%\n"
                    f"Max Open Trades: {config_info.get('max_open_trades', 0)}\n"
                    f"Trading Pairs: {', '.join(config_info.get('pairs', []))}"
                )
            except Exception as e:
                message = f"❌ Error fetching config: {str(e)}"
        else:
            message = "❌ Bot instance not available"

        await update.message.reply_text(message)

    async def cmd_model(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /model command"""
        model_status = await self._call_optional_helper("get_model_status_info")
        if not isinstance(model_status, dict):
            model_status = self._get_model_status_snapshot()
        lines = self._format_model_status_lines(model_status)

        training_status = await self._get_training_job_status()
        if training_status is not None:
            current_job = self._job_value(training_status, "current_job", None)
            last_job = self._job_value(training_status, "last_job", None)
            if current_job is not None:
                lines.extend(
                    self._format_training_job_lines(current_job, heading="Training Job")
                )
            elif last_job is not None:
                lines.extend(
                    self._format_training_job_lines(
                        last_job, heading="Last Training Job"
                    )
                )

        await update.message.reply_text("\n".join(lines))

    async def cmd_train_model(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /train_model command"""
        bot = getattr(self, "bot_instance", None)
        if bot is None:
            await update.message.reply_text("❌ Bot instance not available")
            return

        trading_mode = self._resolve_trading_mode()
        if trading_mode != "paper":
            await update.message.reply_text(
                "❌ Training is paper-only. Switch the bot to paper mode first."
            )
            return

        trading_paused = getattr(bot, "trading_paused", None)
        if trading_paused is None:
            trading_paused = not getattr(bot, "is_running", True)
        if not trading_paused:
            await update.message.reply_text(
                "❌ Pause trading before starting model training."
            )
            return

        request = self._extract_training_request(context)
        if not request.get("pairs"):
            request["pairs"] = self._resolve_default_pairs()
        if not request.get("candles"):
            request["candles"] = self._resolve_default_candles()
        if not request.get("timeframe"):
            request["timeframe"] = self._resolve_default_timeframe()
        request["requested_by"] = "telegram"

        try:
            job = await self._enqueue_training_job(request)
        except Exception as exc:
            await update.message.reply_text(f"❌ Failed to queue training: {exc}")
            return

        if job is None:
            await update.message.reply_text(
                "❌ Training is unavailable. No training store or helper is configured."
            )
            return

        status = self._job_value(job, "status", "unknown")
        lines = self._format_training_job_lines(job, heading="Training Request")
        if status == "blocked":
            lines.insert(1, "Queued request was blocked because another job is active.")
        else:
            lines.insert(1, "Training job queued successfully.")

        await update.message.reply_text("\n".join(lines))

    async def cmd_train_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle /train_status command"""
        training_status = await self._get_training_job_status()
        if training_status is None:
            await update.message.reply_text("❌ Training status is unavailable")
            return

        current_job = self._job_value(training_status, "current_job", None)
        last_job = self._job_value(training_status, "last_job", None)
        can_start = self._job_value(training_status, "can_start_training", False)
        active_job_count = self._job_value(training_status, "active_job_count", 0)

        lines = [
            "🧠 Training Control",
            f"Can Start Training: {'yes' if can_start else 'no'}",
            f"Active Jobs: {active_job_count}",
        ]
        if current_job is not None:
            lines.extend(
                self._format_training_job_lines(current_job, heading="Current Job")
            )
        elif last_job is not None:
            lines.extend(self._format_training_job_lines(last_job, heading="Last Job"))

        await update.message.reply_text("\n".join(lines))

    async def cmd_reconcile(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /reconcile command - manually trigger audit"""
        if hasattr(self, "bot_instance") and hasattr(
            self.bot_instance, "reconciliation"
        ):
            await update.message.reply_text(
                "🔍 Starting manual reconciliation audit..."
            )
            try:
                discrepancies = await self.bot_instance.reconciliation.reconcile()
                orphans = len(discrepancies.get("orphans", []))
                lost = len(discrepancies.get("lost_trades", []))
                mismatches = len(discrepancies.get("mismatches", []))

                if orphans == 0 and lost == 0 and mismatches == 0:
                    await update.message.reply_text(
                        "✅ Reconciliation complete: Local DB matches Exchange state."
                    )
                else:
                    msg = (
                        "⚖️ Reconciliation Results:\n"
                        f"• Orphans: {orphans}\n"
                        f"• Lost Trades: {lost}\n"
                        f"• Mismatches: {mismatches}\n\n"
                        "Check bot logs for specific details."
                    )
                    await update.message.reply_text(msg)
            except Exception as e:
                await update.message.reply_text(f"❌ Reconciliation failed: {str(e)}")
        else:
            await update.message.reply_text("❌ Reconciliation manager not available")

    async def start_webhook(self, webhook_url: str, port: int = 8443):
        """Start webhook for receiving Telegram updates"""
        if not self.enabled or not self.application:
            return

        try:
            await self.application.bot.set_webhook(url=webhook_url)
            await self.application.run_webhook(
                listen="0.0.0.0", port=port, webhook_url=webhook_url
            )
            logger.info(f"Telegram webhook started on port {port}")
        except Exception as e:
            logger.error(f"Error starting Telegram webhook: {e}")

    async def cmd_performance(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /performance command - quick summary"""
        if hasattr(self, "bot_instance") and hasattr(
            self.bot_instance, "performance_tracker"
        ):
            try:
                metrics = (
                    await self.bot_instance.performance_tracker.get_metrics_summary(
                        trade_store=self.bot_instance.trade_store
                    )
                )
                projection = self._get_runtime_projection()
                visibility = self._get_runtime_visibility_info()

                pnl_state = (
                    "profit"
                    if metrics["total_pnl"] > 0
                    else "loss" if metrics["total_pnl"] < 0 else "flat"
                )
                open_positions = metrics.get("open_trades_count", 0)
                if self._uses_runtime_projection(projection):
                    open_positions = getattr(projection, "active_position_count", 0)

                lines = [
                    "Performance Summary",
                    "",
                    f"Balance: ${metrics['current_balance']:,.2f}",
                    f"Total P&L ({pnl_state}): ${metrics['total_pnl']:,.2f} ({metrics['total_return_percent']:.2f}%)",
                    "",
                    f"Trades: {metrics['total_trades']}",
                    f"Win Rate: {metrics['win_rate']:.1f}%",
                    f"Profit Factor: {metrics['profit_factor']:.2f}",
                    f"Max DD: {metrics['max_drawdown_percent']:.2f}%",
                    f"Open Positions: {open_positions}",
                ]

                if projection is not None:
                    if (
                        self._projection_source_value(projection)
                        == ProjectionSource.SHADOW.value
                    ):
                        lines.append(
                            f"Shadow Projected Positions: {getattr(projection, 'active_position_count', 0)}"
                        )
                    lines.extend(self._runtime_summary_lines(projection))
                    lines.extend(
                        self._rollout_summary_for_visibility(
                            projection,
                            visibility=visibility,
                            trading_mode=self._resolve_trading_mode(),
                        )
                    )

                message = "\n".join(lines)
            except Exception as e:
                message = f"Error fetching performance: {str(e)}"
        else:
            message = "Performance tracker not available"

        await update.message.reply_text(message)

    async def cmd_metrics(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /metrics command - detailed metrics"""
        if hasattr(self, "bot_instance") and hasattr(
            self.bot_instance, "performance_tracker"
        ):
            try:
                metrics = (
                    await self.bot_instance.performance_tracker.get_metrics_summary(
                        trade_store=self.bot_instance.trade_store
                    )
                )
                projection = self._get_runtime_projection()
                visibility = self._get_runtime_visibility_info()
                open_positions = metrics.get("open_trades_count", 0)
                if self._uses_runtime_projection(projection):
                    open_positions = getattr(projection, "active_position_count", 0)

                lines = [
                    "Detailed Performance Metrics",
                    "",
                    "BALANCE",
                    f"Initial: ${metrics['initial_balance']:,.2f}",
                    f"Current: ${metrics['current_balance']:,.2f}",
                    f"P&L: ${metrics['total_pnl']:,.2f} ({metrics['total_return_percent']:.2f}%)",
                    "",
                    "TRADES",
                    f"Total: {metrics['total_trades']}",
                    f"Winners: {metrics['winning_trades']}",
                    f"Losers: {metrics['losing_trades']}",
                    f"Win Rate: {metrics['win_rate']:.2f}%",
                    f"Avg Win: ${metrics['average_win']:.2f}",
                    f"Avg Loss: ${metrics['average_loss']:.2f}",
                    f"W/L Ratio: {metrics['win_loss_ratio']:.2f}",
                    "",
                    "RISK METRICS",
                    f"Sharpe Ratio: {metrics['sharpe_ratio']:.3f}",
                    f"Sortino Ratio: {metrics['sortino_ratio']:.3f}",
                    f"Profit Factor: {metrics['profit_factor']:.3f}",
                    f"Expectancy: ${metrics['expectancy']:.2f}",
                    "",
                    "DRAWDOWN",
                    f"Max DD: {metrics['max_drawdown_percent']:.2f}%",
                    f"Current DD: {metrics['current_drawdown_percent']:.2f}%",
                    "",
                    f"Open Positions: {open_positions}",
                ]

                if projection is not None:
                    if (
                        self._projection_source_value(projection)
                        == ProjectionSource.SHADOW.value
                    ):
                        lines.append(
                            f"Shadow Projected Positions: {getattr(projection, 'active_position_count', 0)}"
                        )
                    lines.extend(self._runtime_summary_lines(projection))
                    lines.extend(
                        self._rollout_summary_for_visibility(
                            projection,
                            visibility=visibility,
                            trading_mode=self._resolve_trading_mode(),
                        )
                    )

                message = "\n".join(lines)
            except Exception as e:
                message = f"Error fetching metrics: {str(e)}"
        else:
            message = "Performance tracker not available"

        await update.message.reply_text(message)

    async def cmd_report(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /report command - full formatted report"""
        if hasattr(self, "bot_instance") and hasattr(
            self.bot_instance, "performance_tracker"
        ):
            try:
                report = await self.bot_instance.performance_tracker.generate_performance_report(
                    trade_store=self.bot_instance.trade_store
                )

                # Telegram has 4096 character limit, so we might need to split
                if len(report) > 4000:
                    # Send in chunks
                    chunks = [report[i : i + 4000] for i in range(0, len(report), 4000)]
                    for chunk in chunks:
                        await update.message.reply_text(
                            f"```\n{chunk}\n```", parse_mode="Markdown"
                        )
                else:
                    await update.message.reply_text(
                        f"```\n{report}\n```", parse_mode="Markdown"
                    )
            except Exception as e:
                message = f"❌ Error generating report: {str(e)}"
                await update.message.reply_text(message)
        else:
            await update.message.reply_text("❌ Performance tracker not available")

    async def cmd_risk(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /risk command - show comprehensive risk report"""
        if hasattr(self, "bot_instance") and hasattr(
            self.bot_instance, "advanced_risk"
        ):
            try:
                risk_report = self.bot_instance.advanced_risk.get_risk_report()

                # Circuit breaker status
                cb_status = (
                    "🟢 ACTIVE"
                    if self.bot_instance.circuit_breaker_active
                    else "⚪ Inactive"
                )
                cb_reason = (
                    f"\nReason: {self.bot_instance.circuit_breaker_reason}"
                    if self.bot_instance.circuit_breaker_active
                    else ""
                )

                message = (
                    f"🛡️ Risk Management Report\n\n"
                    f"📊 Circuit Breaker: {cb_status}{cb_reason}\n\n"
                    f"⚙️ Position Sizing:\n"
                    f"  • ATR Period: {risk_report['position_sizing_parameters']['atr_period']}\n"
                    f"  • ATR Multiplier: {risk_report['position_sizing_parameters']['atr_multiplier']}\n"
                    f"  • Kelly Enabled: {risk_report['position_sizing_parameters']['kelly_enabled']}\n"
                    f"  • Kelly Cap: {risk_report['position_sizing_parameters']['kelly_cap']:.2f}\n\n"
                    f"🔗 Correlation Limits:\n"
                    f"  • Threshold: {risk_report['correlation_parameters']['threshold']:.2f}\n"
                    f"  • Max Correlated: {risk_report['correlation_parameters']['max_correlated_positions']}\n\n"
                    f"🚨 Circuit Breakers:\n"
                    f"  • Volatility Threshold: {risk_report['circuit_breaker_parameters']['volatility_threshold']:.1%}\n"
                    f"  • Drawdown Threshold: {risk_report['circuit_breaker_parameters']['drawdown_threshold']:.1%}\n\n"
                    f"📈 Portfolio Risk: {risk_report['portfolio_heatmap']['total_risk']:.2f}%"
                )

                alert_state = await self._get_operational_alert_state()
                if isinstance(alert_state, dict):
                    message += f"\n\n🚦 Entry Halt State: {alert_state.get('halt_state', 'none')}"
                    blocked_reason = alert_state.get("entries_blocked_reason")
                    if blocked_reason:
                        message += f"\nBlocked Reason: {blocked_reason}"
                    critical_alerts = self._critical_alerts(alert_state)
                    if critical_alerts:
                        message += "\nCritical Alerts: " + ", ".join(
                            alert.get("code", "unknown") for alert in critical_alerts
                        )

                await update.message.reply_text(message)
            except Exception as e:
                await update.message.reply_text(
                    f"❌ Error generating risk report: {str(e)}"
                )
        else:
            await update.message.reply_text("❌ Advanced risk manager not available")

    async def cmd_correlations(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        """Handle /correlations command - show asset correlations"""
        if hasattr(self, "bot_instance") and hasattr(
            self.bot_instance, "advanced_risk"
        ):
            try:
                correlations = self.bot_instance.advanced_risk.asset_correlations

                if not correlations:
                    await update.message.reply_text(
                        "📊 No correlation data available yet.\nNeed at least 2 trading pairs with sufficient history."
                    )
                    return

                message = "🔗 Asset Correlations\n\n"

                # Sort by absolute correlation value
                sorted_corr = sorted(
                    correlations.items(), key=lambda x: abs(x[1]), reverse=True
                )

                for (symbol1, symbol2), corr in sorted_corr[:15]:  # Show top 15
                    # Emoji based on correlation strength
                    if abs(corr) > 0.7:
                        emoji = "🔴"  # High correlation
                    elif abs(corr) > 0.4:
                        emoji = "🟡"  # Moderate correlation
                    else:
                        emoji = "🟢"  # Low correlation

                    message += f"{emoji} {symbol1} ↔ {symbol2}: {corr:+.3f}\n"

                if len(sorted_corr) > 15:
                    message += f"\n... and {len(sorted_corr) - 15} more pairs"

                await update.message.reply_text(message)
            except Exception as e:
                await update.message.reply_text(
                    f"❌ Error getting correlations: {str(e)}"
                )
        else:
            await update.message.reply_text("❌ Advanced risk manager not available")

    async def cmd_heatmap(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle /heatmap command - show portfolio risk heatmap"""
        if hasattr(self, "bot_instance") and hasattr(
            self.bot_instance, "advanced_risk"
        ):
            try:
                # Generate heatmap
                balance = await self.bot_instance.exchange.get_balance()
                usdt_balance = balance.get("USDT", {}).get("free", 0) + balance.get(
                    "USDT", {}
                ).get("used", 0)

                heatmap = self.bot_instance.advanced_risk.generate_portfolio_heatmap(
                    positions=self.bot_instance.active_trades,
                    price_data=self.bot_instance.price_data_cache,
                    balance=usdt_balance,
                )

                if not heatmap.asset_exposures:
                    await update.message.reply_text(
                        "📊 No active positions - portfolio heatmap is empty"
                    )
                    return

                message = "🔥 Portfolio Risk Heatmap\n\n"
                message += (
                    f"⚡ Total Portfolio Risk: {heatmap.total_portfolio_risk:.2f}%\n\n"
                )
                message += "📊 Asset Exposures:\n"

                # Sort by exposure
                sorted_exposures = sorted(
                    heatmap.asset_exposures.items(), key=lambda x: x[1], reverse=True
                )

                for symbol, exposure in sorted_exposures:
                    # Visual bar
                    bar_length = int(exposure / 5)  # 1 char per 5%
                    bar = "█" * min(bar_length, 20)

                    message += f"  {symbol}: {exposure:.1f}% {bar}\n"

                # Check if any positions are highly correlated
                high_corr_warning = ""
                for (s1, s2), corr in heatmap.correlations.items():
                    if (
                        abs(corr) > 0.7
                        and s1 in heatmap.asset_exposures
                        and s2 in heatmap.asset_exposures
                    ):
                        high_corr_warning += f"⚠️ {s1} & {s2}: {corr:+.2f}\n"

                if high_corr_warning:
                    message += f"\n⚠️ High Correlations:\n{high_corr_warning}"

                await update.message.reply_text(message)
            except Exception as e:
                await update.message.reply_text(
                    f"❌ Error generating heatmap: {str(e)}"
                )
        else:
            await update.message.reply_text("❌ Advanced risk manager not available")

    async def close(self):
        """Close Telegram connections"""
        if self.application:
            await self.application.stop()
        if hasattr(self, "bot"):
            # Bot doesn't need explicit closing in python-telegram-bot v20+
            pass

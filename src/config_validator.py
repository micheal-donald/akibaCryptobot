"""
Configuration Validator

Validates configuration files for AkibaCryptoBot to ensure all required
parameters are present and have valid values.
"""

import logging
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


class ConfigValidator:
    """Validates bot configuration"""

    def __init__(self):
        self.errors = []
        self.warnings = []

    def validate(self, config: Dict) -> Tuple[bool, List[str], List[str]]:
        """
        Validate configuration

        Args:
            config: Configuration dictionary

        Returns:
            Tuple of (is_valid, errors, warnings)
        """
        self.errors = []
        self.warnings = []

        # Validate each section
        self._validate_exchange(config.get("exchange", {}))
        self._validate_trading(config.get("trading", {}))
        self._validate_risk_management(config.get("risk_management", {}))
        self._validate_advanced_risk(config.get("advanced_risk", {}))
        self._validate_walkforward(config.get("walkforward", {}))
        self._validate_freqai(config.get("freqai", {}))
        self._validate_notifications(config.get("notifications", {}))
        self._validate_logging(config.get("logging", {}))
        self._validate_backtesting(config.get("backtesting", {}))

        is_valid = len(self.errors) == 0
        return is_valid, self.errors, self.warnings

    def _validate_exchange(self, exchange: Dict):
        """Validate exchange configuration"""
        if not exchange:
            self.errors.append("Missing 'exchange' section in config")
            return

        # Optional but recommended fields
        if "sandbox" not in exchange:
            self.warnings.append(
                "'exchange.sandbox' not specified - defaulting to True"
            )

        if "rate_limit" in exchange:
            if (
                not isinstance(exchange["rate_limit"], (int, float))
                or exchange["rate_limit"] <= 0
            ):
                self.errors.append("'exchange.rate_limit' must be a positive number")

    def _validate_trading(self, trading: Dict):
        """Validate trading configuration"""
        if not trading:
            self.errors.append("Missing 'trading' section in config")
            return

        # Required fields
        if "pairs" not in trading:
            self.errors.append(
                "Missing 'trading.pairs' - at least one trading pair required"
            )
        elif not isinstance(trading["pairs"], list) or len(trading["pairs"]) == 0:
            self.errors.append("'trading.pairs' must be a non-empty list")

        if "timeframes" not in trading:
            self.errors.append("Missing 'trading.timeframes'")
        elif "primary" not in trading.get("timeframes", {}):
            self.errors.append("Missing 'trading.timeframes.primary'")

        if "strategy" not in trading:
            self.warnings.append("Missing 'trading.strategy' - must be specified")

        # Optional but important fields
        if "max_open_trades" in trading:
            max_trades = trading["max_open_trades"]
            if not isinstance(max_trades, int) or max_trades < 1:
                self.errors.append(
                    "'trading.max_open_trades' must be a positive integer"
                )
            elif max_trades > 10:
                self.warnings.append(
                    f"'trading.max_open_trades' is {max_trades} - consider reducing for better risk management"
                )

    def _validate_risk_management(self, risk_mgmt: Dict):
        """Validate risk management configuration"""
        if not risk_mgmt:
            self.warnings.append("Missing 'risk_management' section - using defaults")
            return

        # Position sizing
        if "position_size_percent" in risk_mgmt:
            pct = risk_mgmt["position_size_percent"]
            if not isinstance(pct, (int, float)) or pct <= 0 or pct > 100:
                self.errors.append(
                    "'risk_management.position_size_percent' must be between 0 and 100"
                )
            elif pct > 20:
                self.warnings.append(
                    f"'risk_management.position_size_percent' is {pct}% - very aggressive, consider reducing"
                )

        # Stop loss
        if "stop_loss_percent" in risk_mgmt:
            sl = risk_mgmt["stop_loss_percent"]
            if not isinstance(sl, (int, float)) or sl <= 0:
                self.errors.append(
                    "'risk_management.stop_loss_percent' must be positive"
                )
            elif sl < 1:
                self.warnings.append(
                    f"'risk_management.stop_loss_percent' is {sl}% - very tight, may trigger frequently"
                )
            elif sl > 10:
                self.warnings.append(
                    f"'risk_management.stop_loss_percent' is {sl}% - very wide, high risk per trade"
                )

        # Take profit
        if "take_profit_percent" in risk_mgmt:
            tp = risk_mgmt["take_profit_percent"]
            if not isinstance(tp, (int, float)) or tp <= 0:
                self.errors.append(
                    "'risk_management.take_profit_percent' must be positive"
                )

        # Check risk/reward ratio
        sl = risk_mgmt.get("stop_loss_percent", 2.0)
        tp = risk_mgmt.get("take_profit_percent", 4.0)
        if tp < sl:
            self.warnings.append(
                f"Risk/reward ratio is unfavorable (TP: {tp}% < SL: {sl}%)"
            )

        # Max risk
        if "max_risk_per_trade" in risk_mgmt:
            max_risk = risk_mgmt["max_risk_per_trade"]
            if not isinstance(max_risk, (int, float)) or max_risk <= 0 or max_risk > 1:
                self.errors.append(
                    "'risk_management.max_risk_per_trade' must be between 0 and 1"
                )
            elif max_risk > 0.05:
                self.warnings.append(
                    f"'risk_management.max_risk_per_trade' is {max_risk * 100}% - consider reducing to 1-2%"
                )

    def _validate_advanced_risk(self, adv_risk: Dict):
        """Validate advanced risk management configuration"""
        if not adv_risk:
            return  # Advanced risk is optional

        # ATR sizing
        if "use_atr_sizing" in adv_risk and adv_risk["use_atr_sizing"]:
            if "atr_period" in adv_risk:
                period = adv_risk["atr_period"]
                if not isinstance(period, int) or period < 5 or period > 100:
                    self.errors.append(
                        "'advanced_risk.atr_period' must be between 5 and 100"
                    )

            if "atr_multiplier" in adv_risk:
                mult = adv_risk["atr_multiplier"]
                if not isinstance(mult, (int, float)) or mult <= 0:
                    self.errors.append(
                        "'advanced_risk.atr_multiplier' must be positive"
                    )
                elif mult > 5:
                    self.warnings.append(
                        f"'advanced_risk.atr_multiplier' is {mult} - very aggressive"
                    )

        # Kelly Criterion
        if "kelly_enabled" in adv_risk and adv_risk["kelly_enabled"]:
            if "kelly_fractions_cap" in adv_risk:
                cap = adv_risk["kelly_fractions_cap"]
                if not isinstance(cap, (int, float)) or cap <= 0 or cap > 1:
                    self.errors.append(
                        "'advanced_risk.kelly_fractions_cap' must be between 0 and 1"
                    )
                elif cap > 0.5:
                    self.warnings.append(
                        f"'advanced_risk.kelly_fractions_cap' is {cap} - full Kelly is very aggressive, consider half-Kelly (0.5)"
                    )

        # Correlation
        if "correlation_threshold" in adv_risk:
            thresh = adv_risk["correlation_threshold"]
            if not isinstance(thresh, (int, float)) or thresh < 0 or thresh > 1:
                self.errors.append(
                    "'advanced_risk.correlation_threshold' must be between 0 and 1"
                )

        if "max_correlated_positions" in adv_risk:
            max_corr = adv_risk["max_correlated_positions"]
            if not isinstance(max_corr, int) or max_corr < 0:
                self.errors.append(
                    "'advanced_risk.max_correlated_positions' must be a non-negative integer"
                )

        # Circuit breakers
        if "volatility_threshold" in adv_risk:
            vol_thresh = adv_risk["volatility_threshold"]
            if not isinstance(vol_thresh, (int, float)) or vol_thresh <= 0:
                self.errors.append(
                    "'advanced_risk.volatility_threshold' must be positive"
                )

        if "drawdown_threshold" in adv_risk:
            dd_thresh = adv_risk["drawdown_threshold"]
            if (
                not isinstance(dd_thresh, (int, float))
                or dd_thresh <= 0
                or dd_thresh > 1
            ):
                self.errors.append(
                    "'advanced_risk.drawdown_threshold' must be between 0 and 1"
                )
            elif dd_thresh > 0.5:
                self.warnings.append(
                    f"'advanced_risk.drawdown_threshold' is {dd_thresh * 100}% - very high, circuit breaker may trigger too late"
                )

    def _validate_walkforward(self, walkforward: Dict):
        """Validate walk-forward configuration"""
        if not walkforward:
            return  # Walk-forward is optional

        if "n_splits" in walkforward:
            splits = walkforward["n_splits"]
            if not isinstance(splits, int) or splits < 2:
                self.errors.append("'walkforward.n_splits' must be at least 2")
            elif splits > 20:
                self.warnings.append(
                    f"'walkforward.n_splits' is {splits} - may be too many, consider 5-10"
                )

        if "train_period" in walkforward:
            train = walkforward["train_period"]
            if not isinstance(train, int) or train < 30:
                self.errors.append(
                    "'walkforward.train_period' must be at least 30 days"
                )

        if "test_period" in walkforward:
            test = walkforward["test_period"]
            if not isinstance(test, int) or test < 7:
                self.errors.append("'walkforward.test_period' must be at least 7 days")

        # Check train/test ratio
        train = walkforward.get("train_period", 180)
        test = walkforward.get("test_period", 60)
        ratio = train / test if test > 0 else 0
        if ratio < 2:
            self.warnings.append(
                f"Train/test ratio is {ratio:.1f} - consider using more training data (3:1 or 4:1 ratio recommended)"
            )

    def _validate_freqai(self, freqai: Dict):
        """Validate FreqAI configuration"""
        if not freqai or not freqai.get("enabled"):
            return  # FreqAI is optional

        if "model_type" in freqai:
            valid_models = [
                "LightGBMClassifier",
                "XGBoostClassifier",
                "CatBoostClassifier",
                "LightGBMRegressor",
                "XGBoostRegressor",
                "CatBoostRegressor",
            ]
            if freqai["model_type"] not in valid_models:
                self.warnings.append(
                    f"'freqai.model_type' is '{freqai['model_type']}' - supported: {valid_models}"
                )

        if "feature_parameters" in freqai:
            feat_params = freqai["feature_parameters"]

            if "label_period_candles" in feat_params:
                label_period = feat_params["label_period_candles"]
                if not isinstance(label_period, int) or label_period < 1:
                    self.errors.append(
                        "'freqai.feature_parameters.label_period_candles' must be a positive integer"
                    )

            if "include_timeframes" in feat_params:
                timeframes = feat_params["include_timeframes"]
                if not isinstance(timeframes, list) or len(timeframes) == 0:
                    self.warnings.append(
                        "'freqai.feature_parameters.include_timeframes' should include multiple timeframes"
                    )

    def _validate_notifications(self, notifications: Dict):
        """Validate notifications configuration"""
        if not notifications:
            return  # Notifications are optional

        if notifications.get("telegram_enabled"):
            # Check for required environment variables (can't validate values here)
            self.warnings.append(
                "Ensure TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are set in .env file"
            )

    def _validate_logging(self, logging_config: Dict):
        """Validate logging configuration"""
        if not logging_config:
            return  # Logging config is optional

        if "level" in logging_config:
            valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
            if logging_config["level"] not in valid_levels:
                self.errors.append(f"'logging.level' must be one of {valid_levels}")

    def _validate_backtesting(self, backtesting: Dict):
        """Validate backtesting configuration"""
        if not backtesting:
            return  # Backtesting is optional

        if "initial_balance" in backtesting:
            balance = backtesting["initial_balance"]
            if not isinstance(balance, (int, float)) or balance <= 0:
                self.errors.append("'backtesting.initial_balance' must be positive")

        if "maker_fee" in backtesting:
            fee = backtesting["maker_fee"]
            if not isinstance(fee, (int, float)) or fee < 0 or fee > 0.01:
                self.errors.append(
                    "'backtesting.maker_fee' must be between 0 and 0.01 (0-1%)"
                )

        if "taker_fee" in backtesting:
            fee = backtesting["taker_fee"]
            if not isinstance(fee, (int, float)) or fee < 0 or fee > 0.01:
                self.errors.append(
                    "'backtesting.taker_fee' must be between 0 and 0.01 (0-1%)"
                )

    def print_validation_report(self):
        """Print validation report"""
        print("\n" + "=" * 70)
        print("CONFIGURATION VALIDATION REPORT")
        print("=" * 70)

        if self.errors:
            print("\n❌ ERRORS:")
            for i, error in enumerate(self.errors, 1):
                print(f"  {i}. {error}")
        else:
            print("\n✅ No errors found")

        if self.warnings:
            print("\n⚠️  WARNINGS:")
            for i, warning in enumerate(self.warnings, 1):
                print(f"  {i}. {warning}")
        else:
            print("\n✅ No warnings")

        print("\n" + "=" * 70)

        if self.errors:
            print("❌ Configuration is INVALID - please fix errors before running")
        elif self.warnings:
            print("⚠️  Configuration is VALID but has warnings - review recommended")
        else:
            print("✅ Configuration is VALID - ready to run")

        print("=" * 70 + "\n")


def validate_config_file(config_path: str) -> bool:
    """
    Validate configuration file

    Args:
        config_path: Path to configuration file

    Returns:
        True if valid, False otherwise
    """
    import json

    try:
        with open(config_path, "r") as f:
            config = json.load(f)
    except FileNotFoundError:
        print(f"❌ Config file not found: {config_path}")
        return False
    except json.JSONDecodeError as e:
        print(f"❌ Invalid JSON in config file: {e}")
        return False

    validator = ConfigValidator()
    is_valid, errors, warnings = validator.validate(config)
    validator.print_validation_report()

    return is_valid


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        config_path = sys.argv[1]
    else:
        config_path = "config/config.json"

    print(f"Validating configuration: {config_path}")
    is_valid = validate_config_file(config_path)

    sys.exit(0 if is_valid else 1)

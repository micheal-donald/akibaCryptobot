"""Credential-free, dry-run-only entrypoint for the deployed engine."""

import json
import os


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(config):
    require(config["dry_run"] is True, "Dry-run must be enabled")
    require(config["trading_mode"] == "spot", "Spot only")
    require(config["dry_run_wallet"] == 1000, "Expected experiment wallet")
    require(0 < config["stake_amount"] <= 50, "Maximum stake is 50 USDT")
    require(0 < config["max_open_trades"] <= 2, "Maximum two positions")
    require(not config["exchange"].get("key"), "No exchange credentials allowed")
    require(not config["exchange"].get("secret"), "No exchange credentials allowed")
    require(not config.get("force_entry_enable"), "Forced entries disabled")


def validate_secrets(secrets):
    require(set(secrets) == {"api_server"}, "Unexpected secrets configuration")
    require(
        set(secrets["api_server"])
        == {"username", "password", "jwt_secret_key", "ws_token"},
        "Only API credentials may be supplied",
    )
    require(all(secrets["api_server"].values()), "API credentials must not be empty")


if __name__ == "__main__":
    with open("/app/deploy/freqtrade/config.json") as stream:
        validate(json.load(stream))
    with open("/run/secrets/freqtrade-api.json") as stream:
        validate_secrets(json.load(stream))
    require(
        not any(k.startswith("FREQTRADE__") for k in os.environ),
        "Environment overrides are disabled for this paper deployment",
    )
    os.execvp(
        "freqtrade",
        [
            "freqtrade",
            "trade",
            "--dry-run",
            "--config",
            "/app/deploy/freqtrade/config.json",
            "--config",
            "/run/secrets/freqtrade-api.json",
            "--strategy-path",
            "/app/deploy/freqtrade/strategies",
            "--userdir",
            "/freqtrade/user_data",
        ],
    )

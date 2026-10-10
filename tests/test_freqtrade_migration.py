"""Safety and public-boundary regression checks for the replacement service."""

import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from deploy.freqtrade import portfolio
from deploy.freqtrade.start import validate


class MigrationTests(unittest.TestCase):
    def test_paper_config_rejects_live_or_credentials(self):
        config = json.loads(Path("deploy/freqtrade/config.json").read_text())
        validate(config)
        for key, value in [
            ("dry_run", False),
            ("stake_amount", 100),
            ("max_open_trades", 3),
        ]:
            altered = copy.deepcopy(config)
            altered[key] = value
            with self.assertRaises(ValueError):
                validate(altered)
        config["exchange"]["key"] = "must-not-be-used"
        with self.assertRaises(ValueError):
            validate(config)

    def test_health_requires_fresh_running_paper_engine_and_redacts(self):
        config = {"dry_run": True, "state": "running", "password": "secret"}
        args = [{"last_process_ts": 995}, {}, [], {"trades": []}, {}, {}, 1000]
        result = portfolio.project(config, *args)
        self.assertTrue(result["healthy"])
        self.assertNotIn("secret", json.dumps(result))
        for key, value in [("dry_run", False), ("state", "stopped")]:
            self.assertFalse(
                portfolio.project({**config, key: value}, *args)["healthy"]
            )
        args[0] = {"last_process_ts": 800}
        self.assertFalse(portfolio.project(config, *args)["healthy"])

    def test_public_has_no_control_or_proxy_routes(self):
        with TestClient(portfolio.app) as client:
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                for path in (
                    "/api/v1/forceenter",
                    "/api/trading/resume",
                    "/api/portfolio",
                ):
                    self.assertEqual(client.request(method, path).status_code, 403)
            for path in (
                "/api/v1/show_config",
                "/api/v1/logs",
                "/docs",
                "/openapi.json",
                "/.env",
            ):
                self.assertEqual(client.get(path).status_code, 404)
            self.assertEqual(client.get("/").status_code, 200)
            with patch.object(portfolio, "snapshot", return_value={"healthy": False}):
                self.assertEqual(client.get("/api/health").status_code, 503)

    def test_api_failure_is_cached_without_leaking_details(self):
        portfolio._expires = 0
        with patch.object(
            portfolio, "upstream", side_effect=RuntimeError("secret")
        ) as upstream:
            result = portfolio.snapshot()
            self.assertFalse(result["healthy"])
            self.assertNotIn("secret", json.dumps(result))
            self.assertEqual(portfolio.snapshot(), result)
            self.assertEqual(upstream.call_count, 1)
        portfolio._expires = 0

    def test_strategy_indicators_are_causal_and_live_is_rejected(self):
        import numpy as np
        import pandas as pd

        path = Path("deploy/freqtrade/strategies/AkibaADXPaper.py")
        spec = importlib.util.spec_from_file_location("akiba_paper_test", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        strategy = module.AkibaADXPaper({"dry_run": True, "trading_mode": "spot"})
        strategy.bot_start()
        strategy.config["dry_run"] = False
        with self.assertRaises(ValueError):
            strategy.bot_start()
        rng = np.random.default_rng(123)
        close = 100 + np.cumsum(rng.normal(size=320))
        frame = pd.DataFrame(
            {
                "open": close,
                "close": close,
                "high": close + 1,
                "low": close - 1,
                "volume": np.ones(320),
            }
        )
        full = strategy.populate_indicators(frame.copy(), {})
        prefix = strategy.populate_indicators(frame.iloc[:250].copy(), {})
        pd.testing.assert_frame_equal(full.iloc[:250], prefix)
        signals = pd.DataFrame(
            {
                "adx": [30, 30, 30],
                "mom": [2, -2, 2],
                "plus_di": [35, 10, 35],
                "minus_di": [10, 35, 10],
                "volume": [1, 1, 0],
            }
        )
        entries = strategy.populate_entry_trend(signals.copy(), {})
        exits = strategy.populate_exit_trend(signals.copy(), {})
        self.assertEqual(entries.enter_long.fillna(0).tolist(), [1, 0, 0])
        self.assertEqual(exits.exit_long.fillna(0).tolist(), [0, 1, 0])


if __name__ == "__main__":
    unittest.main()

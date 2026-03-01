"""
Tests for performance metrics tracker
"""

import pytest
from datetime import datetime, timedelta
from performance.metrics_tracker import PerformanceMetricsTracker, Trade


class TestPerformanceMetricsTracker:
    """Test suite for PerformanceMetricsTracker"""

    def test_initialization(self):
        """Test tracker initialization"""
        tracker = PerformanceMetricsTracker(initial_balance=10000.0)

        assert tracker.initial_balance == 10000.0
        assert tracker.current_balance == 10000.0
        assert len(tracker.trades) == 0
        assert len(tracker.open_trades) == 0

    def test_record_trade_entry(self):
        """Test recording trade entry"""
        tracker = PerformanceMetricsTracker(initial_balance=10000.0)

        tracker.record_trade_entry(
            symbol="BTC/USDT",
            side="buy",
            amount=0.1,
            entry_price=50000.0,
            strategy="momentum",
        )

        assert "BTC/USDT" in tracker.open_trades
        assert tracker.open_trades["BTC/USDT"]["side"] == "buy"
        assert tracker.open_trades["BTC/USDT"]["amount"] == 0.1
        assert tracker.open_trades["BTC/USDT"]["entry_price"] == 50000.0

    def test_record_winning_trade(self):
        """Test recording a winning trade"""
        tracker = PerformanceMetricsTracker(initial_balance=10000.0)

        # Entry
        tracker.record_trade_entry(
            symbol="BTC/USDT",
            side="buy",
            amount=0.1,
            entry_price=50000.0,
            strategy="momentum",
        )

        # Exit at profit
        trade = tracker.record_trade_exit(
            symbol="BTC/USDT",
            exit_price=52000.0,  # 4% profit
            exit_reason="take_profit",
            fees=5.0,
        )

        assert trade is not None
        assert trade.is_winner
        assert trade.pnl > 0
        assert trade.pnl == (52000.0 - 50000.0) * 0.1 - 5.0  # 195.0
        assert tracker.current_balance > 10000.0
        assert len(tracker.trades) == 1
        assert "BTC/USDT" not in tracker.open_trades

    def test_record_losing_trade(self):
        """Test recording a losing trade"""
        tracker = PerformanceMetricsTracker(initial_balance=10000.0)

        # Entry
        tracker.record_trade_entry(
            symbol="ETH/USDT",
            side="buy",
            amount=1.0,
            entry_price=3000.0,
            strategy="momentum",
        )

        # Exit at loss
        trade = tracker.record_trade_exit(
            symbol="ETH/USDT",
            exit_price=2900.0,  # ~3.3% loss
            exit_reason="stop_loss",
            fees=2.9,
        )

        assert trade is not None
        assert not trade.is_winner
        assert trade.pnl < 0
        assert trade.pnl == (2900.0 - 3000.0) * 1.0 - 2.9  # -102.9
        assert tracker.current_balance < 10000.0

    def test_win_rate_calculation(self):
        """Test win rate calculation"""
        tracker = PerformanceMetricsTracker(initial_balance=10000.0)

        # Simulate 3 winning trades and 2 losing trades
        test_trades = [
            ("BTC/USDT", "buy", 0.1, 50000.0, 51000.0, "take_profit"),  # Win
            ("ETH/USDT", "buy", 1.0, 3000.0, 3100.0, "take_profit"),  # Win
            ("BNB/USDT", "buy", 10.0, 300.0, 290.0, "stop_loss"),  # Loss
            ("ADA/USDT", "buy", 100.0, 1.0, 1.05, "take_profit"),  # Win
            ("SOL/USDT", "buy", 5.0, 100.0, 95.0, "stop_loss"),  # Loss
        ]

        for symbol, side, amount, entry, exit_price, reason in test_trades:
            tracker.record_trade_entry(symbol, side, amount, entry, "test")
            tracker.record_trade_exit(symbol, exit_price, reason, 0.0)

        win_rate = tracker.calculate_win_rate()
        assert win_rate == 60.0  # 3 out of 5

    def test_profit_factor(self):
        """Test profit factor calculation"""
        tracker = PerformanceMetricsTracker(initial_balance=10000.0)

        # Win $200
        tracker.record_trade_entry("BTC/USDT", "buy", 0.1, 50000.0, "test")
        tracker.record_trade_exit("BTC/USDT", 52000.0, "win", 0.0)

        # Lose $100
        tracker.record_trade_entry("ETH/USDT", "buy", 1.0, 3000.0, "test")
        tracker.record_trade_exit("ETH/USDT", 2900.0, "loss", 0.0)

        profit_factor = tracker.calculate_profit_factor()
        assert profit_factor == 2.0  # 200 / 100

    def test_sharpe_ratio_calculation(self):
        """Test Sharpe ratio calculation"""
        tracker = PerformanceMetricsTracker(initial_balance=10000.0)

        # Simulate some trades to create returns
        tracker.daily_returns = [0.01, 0.02, -0.01, 0.015, 0.03]

        sharpe = tracker.calculate_sharpe_ratio()
        assert sharpe != 0.0  # Should return a value
        assert isinstance(sharpe, float)

    def test_sortino_ratio_calculation(self):
        """Test Sortino ratio calculation"""
        tracker = PerformanceMetricsTracker(initial_balance=10000.0)

        # Simulate returns with both positive and negative
        tracker.daily_returns = [0.02, -0.01, 0.03, -0.015, 0.01]

        sortino = tracker.calculate_sortino_ratio()
        assert isinstance(sortino, float)

    def test_max_drawdown_tracking(self):
        """Test maximum drawdown tracking"""
        tracker = PerformanceMetricsTracker(initial_balance=10000.0)

        # Simulate balance changes that create drawdown
        tracker.update_balance(11000.0)  # New peak
        tracker.update_balance(10500.0)  # Drawdown
        tracker.update_balance(10000.0)  # Further drawdown

        # Max drawdown should be ~9.09% from peak of 11000 to 10000
        assert tracker.max_drawdown > 0
        assert tracker.max_drawdown < 0.1  # Should be less than 10%

    def test_expectancy_calculation(self):
        """Test expectancy (average PnL per trade)"""
        tracker = PerformanceMetricsTracker(initial_balance=10000.0)

        # Trade 1: +100
        tracker.record_trade_entry("BTC/USDT", "buy", 0.1, 50000.0, "test")
        tracker.record_trade_exit("BTC/USDT", 51000.0, "win", 0.0)

        # Trade 2: +50
        tracker.record_trade_entry("ETH/USDT", "buy", 1.0, 3000.0, "test")
        tracker.record_trade_exit("ETH/USDT", 3050.0, "win", 0.0)

        # Trade 3: -30
        tracker.record_trade_entry("BNB/USDT", "buy", 10.0, 300.0, "test")
        tracker.record_trade_exit("BNB/USDT", 297.0, "loss", 0.0)

        expectancy = tracker.calculate_expectancy()
        assert expectancy == (100 + 50 - 30) / 3  # 40.0

    def test_average_win_loss_ratio(self):
        """Test average win/loss ratio calculation"""
        tracker = PerformanceMetricsTracker(initial_balance=10000.0)

        # Win $200
        tracker.record_trade_entry("BTC/USDT", "buy", 0.1, 50000.0, "test")
        tracker.record_trade_exit("BTC/USDT", 52000.0, "win", 0.0)

        # Win $100
        tracker.record_trade_entry("ETH/USDT", "buy", 1.0, 3000.0, "test")
        tracker.record_trade_exit("ETH/USDT", 3100.0, "win", 0.0)

        # Lose $50
        tracker.record_trade_entry("BNB/USDT", "buy", 10.0, 300.0, "test")
        tracker.record_trade_exit("BNB/USDT", 295.0, "loss", 0.0)

        avg_win, avg_loss, ratio = tracker.calculate_average_win_loss_ratio()

        assert avg_win == 150.0  # (200 + 100) / 2
        assert avg_loss == 50.0
        assert ratio == 3.0  # 150 / 50

    def test_metrics_summary(self):
        """Test comprehensive metrics summary"""
        tracker = PerformanceMetricsTracker(initial_balance=10000.0)

        # Add some trades
        tracker.record_trade_entry("BTC/USDT", "buy", 0.1, 50000.0, "momentum")
        tracker.record_trade_exit("BTC/USDT", 51000.0, "win", 5.0)

        tracker.record_trade_entry("ETH/USDT", "buy", 1.0, 3000.0, "momentum")
        tracker.record_trade_exit("ETH/USDT", 2950.0, "loss", 3.0)

        summary = tracker.get_metrics_summary()

        assert "initial_balance" in summary
        assert "current_balance" in summary
        assert "total_pnl" in summary
        assert "total_trades" in summary
        assert "win_rate" in summary
        assert "sharpe_ratio" in summary
        assert "sortino_ratio" in summary
        assert "profit_factor" in summary
        assert "max_drawdown_percent" in summary

        assert summary["total_trades"] == 2
        assert summary["winning_trades"] == 1
        assert summary["losing_trades"] == 1

    def test_strategy_performance_tracking(self):
        """Test per-strategy performance tracking"""
        tracker = PerformanceMetricsTracker(initial_balance=10000.0)

        # Trades for momentum strategy
        tracker.record_trade_entry("BTC/USDT", "buy", 0.1, 50000.0, "momentum")
        tracker.record_trade_exit("BTC/USDT", 51000.0, "win", 0.0)

        # Trades for mean_reversion strategy
        tracker.record_trade_entry("ETH/USDT", "buy", 1.0, 3000.0, "mean_reversion")
        tracker.record_trade_exit("ETH/USDT", 3100.0, "win", 0.0)

        momentum_perf = tracker.get_strategy_performance("momentum")
        mean_rev_perf = tracker.get_strategy_performance("mean_reversion")

        assert momentum_perf["total_trades"] == 1
        assert mean_rev_perf["total_trades"] == 1
        assert momentum_perf["total_pnl"] == 100.0
        assert mean_rev_perf["total_pnl"] == 100.0

    def test_equity_curve_data(self):
        """Test equity curve data export"""
        tracker = PerformanceMetricsTracker(initial_balance=10000.0)

        tracker.record_trade_entry("BTC/USDT", "buy", 0.1, 50000.0, "test")
        tracker.record_trade_exit("BTC/USDT", 51000.0, "win", 0.0)

        df = tracker.get_equity_curve_data()

        assert len(df) > 0
        assert "timestamp" in df.columns
        assert "balance" in df.columns

    def test_trades_export_to_dataframe(self):
        """Test exporting trades to DataFrame"""
        tracker = PerformanceMetricsTracker(initial_balance=10000.0)

        tracker.record_trade_entry("BTC/USDT", "buy", 0.1, 50000.0, "test")
        tracker.record_trade_exit("BTC/USDT", 51000.0, "win", 5.0)

        df = tracker.export_trades_to_dataframe()

        assert len(df) == 1
        assert "symbol" in df.columns
        assert "pnl" in df.columns
        assert "exit_reason" in df.columns
        assert df.iloc[0]["symbol"] == "BTC/USDT"

    def test_performance_report_generation(self):
        """Test formatted performance report"""
        tracker = PerformanceMetricsTracker(initial_balance=10000.0)

        tracker.record_trade_entry("BTC/USDT", "buy", 0.1, 50000.0, "momentum")
        tracker.record_trade_exit("BTC/USDT", 51000.0, "win", 0.0)

        report = tracker.generate_performance_report()

        assert isinstance(report, str)
        assert "PERFORMANCE REPORT" in report
        assert "BALANCE & RETURNS" in report
        assert "TRADE STATISTICS" in report
        assert "RISK-ADJUSTED METRICS" in report

    def test_reset_functionality(self):
        """Test tracker reset"""
        tracker = PerformanceMetricsTracker(initial_balance=10000.0)

        # Add some data
        tracker.record_trade_entry("BTC/USDT", "buy", 0.1, 50000.0, "test")
        tracker.record_trade_exit("BTC/USDT", 51000.0, "win", 0.0)

        # Reset
        tracker.reset()

        assert len(tracker.trades) == 0
        assert len(tracker.open_trades) == 0
        assert tracker.current_balance == tracker.initial_balance
        assert tracker.max_drawdown == 0.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

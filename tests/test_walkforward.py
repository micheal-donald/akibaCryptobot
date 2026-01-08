import pytest
import asyncio
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from unittest.mock import Mock, AsyncMock, patch
import sys
import os

# Add src to path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'src'))

from backtesting.walkforward import WalkForwardAnalyzer, WalkForwardResult
from backtesting.engine import BacktestEngine, BacktestResult, BacktestTrade
from strategies.base_strategy import BaseStrategy, TradingSignal, SignalType


# Mock strategy for testing
class MockStrategy(BaseStrategy):
    """Mock strategy for testing walk-forward analysis"""

    def __init__(self):
        # Pass a config dict, not a string
        super().__init__({'timeframe': '1h', 'lookback_period': 100})
        self.trained_symbols = []
        self.train_call_count = 0

    async def generate_signal(self, symbol: str, ohlcv_data: pd.DataFrame, current_price: float) -> TradingSignal:
        """Generate a simple buy signal for testing"""
        return TradingSignal(
            signal_type=SignalType.BUY,
            strength=0.8,
            price=current_price,
            timestamp=datetime.now(),
            metadata={'test': True}
        )

    def calculate_indicators(self, ohlcv_data: pd.DataFrame) -> pd.DataFrame:
        """Return dataframe as-is for testing"""
        return ohlcv_data

    async def train_model(self, symbol: str, data: pd.DataFrame):
        """Mock training method"""
        self.trained_symbols.append(symbol)
        self.train_call_count += 1
        await asyncio.sleep(0.01)  # Simulate some work


# Test fixtures
@pytest.fixture
def sample_config():
    """Sample configuration for walk-forward analyzer"""
    return {
        'walkforward': {
            'n_splits': 3,
            'train_period': 60,  # 60 days
            'test_period': 30,   # 30 days
            'retrain_period': 2
        },
        'backtesting': {
            'initial_balance': 10000.0,
            'maker_fee': 0.001,
            'taker_fee': 0.001
        }
    }


@pytest.fixture
def sample_ohlcv_data():
    """Generate sample OHLCV data for testing"""
    dates = pd.date_range(start='2024-01-01', end='2024-12-31', freq='1h')

    # Generate realistic price data
    np.random.seed(42)
    price = 100.0
    prices = [price]

    for _ in range(len(dates) - 1):
        change = np.random.randn() * 0.02  # 2% volatility
        price = price * (1 + change)
        prices.append(price)

    df = pd.DataFrame({
        'open': prices,
        'high': [p * (1 + abs(np.random.randn() * 0.01)) for p in prices],
        'low': [p * (1 - abs(np.random.randn() * 0.01)) for p in prices],
        'close': prices,
        'volume': [1000000 * (1 + np.random.randn() * 0.1) for _ in prices]
    }, index=dates)

    return df


@pytest.fixture
def sample_multi_symbol_data(sample_ohlcv_data):
    """Generate multi-symbol data"""
    btc_data = sample_ohlcv_data.copy()
    eth_data = sample_ohlcv_data.copy() * 0.95  # Slightly different prices

    return {
        'BTC/USDT': btc_data,
        'ETH/USDT': eth_data
    }


# Test WalkForwardAnalyzer initialization
def test_walkforward_analyzer_init(sample_config):
    """Test that WalkForwardAnalyzer initializes correctly"""
    analyzer = WalkForwardAnalyzer(sample_config)

    assert analyzer.n_splits == 3
    assert analyzer.train_period == 60
    assert analyzer.test_period == 30
    assert analyzer.retrain_period == 2


def test_walkforward_analyzer_default_config():
    """Test analyzer with default configuration"""
    analyzer = WalkForwardAnalyzer({})

    assert analyzer.n_splits == 5  # Default
    assert analyzer.train_period == 180  # Default
    assert analyzer.test_period == 60  # Default


# Test data combination
def test_combine_data(sample_config, sample_multi_symbol_data):
    """Test combining data from multiple symbols"""
    analyzer = WalkForwardAnalyzer(sample_config)
    symbols = ['BTC/USDT', 'ETH/USDT']

    combined = analyzer._combine_data(sample_multi_symbol_data, symbols)

    assert not combined.empty
    assert 'symbol' in combined.columns
    assert len(combined) > 0
    assert set(combined['symbol'].unique()) == set(symbols)


def test_combine_data_empty(sample_config):
    """Test combining data with no symbols"""
    analyzer = WalkForwardAnalyzer(sample_config)

    combined = analyzer._combine_data({}, [])

    assert combined.empty


def test_combine_data_missing_symbol(sample_config, sample_multi_symbol_data):
    """Test combining data when a symbol is missing"""
    analyzer = WalkForwardAnalyzer(sample_config)
    symbols = ['BTC/USDT', 'XRP/USDT']  # XRP not in data

    combined = analyzer._combine_data(sample_multi_symbol_data, symbols)

    assert not combined.empty
    assert 'BTC/USDT' in combined['symbol'].values
    assert 'XRP/USDT' not in combined['symbol'].values


# Test walk-forward period creation
def test_create_walkforward_periods(sample_config, sample_multi_symbol_data):
    """Test creation of walk-forward periods"""
    analyzer = WalkForwardAnalyzer(sample_config)
    symbols = ['BTC/USDT', 'ETH/USDT']
    combined = analyzer._combine_data(sample_multi_symbol_data, symbols)

    periods = analyzer._create_walkforward_periods(combined)

    assert len(periods) == 3  # n_splits

    # Check that each period has the correct structure
    for train_start, train_end, test_start, test_end in periods:
        assert isinstance(train_start, datetime)
        assert isinstance(train_end, datetime)
        assert isinstance(test_start, datetime)
        assert isinstance(test_end, datetime)
        assert train_start < train_end
        assert test_start <= test_end
        assert train_end == test_start  # Test starts right after train


def test_create_walkforward_periods_empty_data(sample_config):
    """Test period creation with empty data"""
    analyzer = WalkForwardAnalyzer(sample_config)

    periods = analyzer._create_walkforward_periods(pd.DataFrame())

    assert periods == []


# Test data filtering
def test_filter_data_by_time(sample_config, sample_multi_symbol_data):
    """Test filtering data by time period"""
    analyzer = WalkForwardAnalyzer(sample_config)

    start_time = datetime(2024, 3, 1)
    end_time = datetime(2024, 6, 1)

    filtered = analyzer._filter_data_by_time(sample_multi_symbol_data, start_time, end_time)

    assert 'BTC/USDT' in filtered
    assert 'ETH/USDT' in filtered

    # Check that all data is within time range
    for symbol, df in filtered.items():
        assert df.index.min() >= start_time
        assert df.index.max() <= end_time


def test_filter_data_by_time_no_overlap(sample_config, sample_multi_symbol_data):
    """Test filtering with no data overlap"""
    analyzer = WalkForwardAnalyzer(sample_config)

    # Use time range before data exists
    start_time = datetime(2020, 1, 1)
    end_time = datetime(2020, 2, 1)

    filtered = analyzer._filter_data_by_time(sample_multi_symbol_data, start_time, end_time)

    assert len(filtered) == 0  # No data should match


# Test train/test data combination
def test_combine_train_test_data(sample_config, sample_multi_symbol_data):
    """Test combining train and test data"""
    analyzer = WalkForwardAnalyzer(sample_config)

    start_time = datetime(2024, 1, 1)
    mid_time = datetime(2024, 6, 1)
    end_time = datetime(2024, 12, 31)

    train_data = analyzer._filter_data_by_time(sample_multi_symbol_data, start_time, mid_time)
    test_data = analyzer._filter_data_by_time(sample_multi_symbol_data, mid_time, end_time)

    combined = analyzer._combine_train_test_data(train_data, test_data)

    assert 'BTC/USDT' in combined
    assert 'ETH/USDT' in combined

    # Check that combined data includes both periods
    for symbol in combined:
        assert len(combined[symbol]) > len(train_data[symbol])
        assert len(combined[symbol]) > len(test_data[symbol])


# Test metric calculations
def test_calculate_max_drawdown(sample_config):
    """Test maximum drawdown calculation"""
    analyzer = WalkForwardAnalyzer(sample_config)

    # Equity curve with known drawdown
    equity_curve = [10000, 11000, 10500, 9000, 9500, 12000]

    max_dd = analyzer._calculate_max_drawdown(equity_curve)

    # Peak was 11000, trough was 9000
    # Drawdown = (11000 - 9000) / 11000 * 100 = 18.18%
    assert max_dd > 0
    assert 18 < max_dd < 19


def test_calculate_max_drawdown_no_drawdown(sample_config):
    """Test drawdown with always increasing equity"""
    analyzer = WalkForwardAnalyzer(sample_config)

    equity_curve = [10000, 11000, 12000, 13000]

    max_dd = analyzer._calculate_max_drawdown(equity_curve)

    assert max_dd == 0.0


def test_calculate_max_drawdown_empty(sample_config):
    """Test drawdown with empty equity curve"""
    analyzer = WalkForwardAnalyzer(sample_config)

    max_dd = analyzer._calculate_max_drawdown([])

    assert max_dd == 0.0


def test_calculate_sharpe_ratio(sample_config):
    """Test Sharpe ratio calculation"""
    analyzer = WalkForwardAnalyzer(sample_config)

    # Consistent positive returns
    returns = [0.01, 0.02, 0.015, 0.018, 0.012]

    sharpe = analyzer._calculate_sharpe_ratio(returns)

    assert sharpe > 0  # Positive returns should give positive Sharpe


def test_calculate_sharpe_ratio_negative_returns(sample_config):
    """Test Sharpe ratio with negative returns"""
    analyzer = WalkForwardAnalyzer(sample_config)

    returns = [-0.01, -0.02, -0.015]

    sharpe = analyzer._calculate_sharpe_ratio(returns)

    assert sharpe < 0  # Negative returns should give negative Sharpe


def test_calculate_sharpe_ratio_zero_volatility(sample_config):
    """Test Sharpe ratio with zero volatility"""
    analyzer = WalkForwardAnalyzer(sample_config)

    returns = [0.01, 0.01, 0.01, 0.01]  # No variance

    sharpe = analyzer._calculate_sharpe_ratio(returns)

    assert sharpe == 0.0  # Should handle zero std dev


def test_calculate_sharpe_ratio_empty(sample_config):
    """Test Sharpe ratio with no returns"""
    analyzer = WalkForwardAnalyzer(sample_config)

    sharpe = analyzer._calculate_sharpe_ratio([])

    assert sharpe == 0.0


# Test overfitting score
def test_calculate_overfitting_score(sample_config):
    """Test overfitting score calculation"""
    analyzer = WalkForwardAnalyzer(sample_config)

    # Create mock results with performance degradation
    in_sample_results = [
        Mock(win_rate=80.0, total_trades=100),
        Mock(win_rate=75.0, total_trades=100)
    ]
    out_of_sample_results = [
        Mock(win_rate=55.0, total_trades=50),
        Mock(win_rate=50.0, total_trades=50)
    ]

    overfitting_score = analyzer._calculate_overfitting_score(in_sample_results, out_of_sample_results)

    # Avg in-sample: 77.5%, avg out-of-sample: 52.5%
    # Degradation: 1 - (52.5 / 77.5) = 0.32
    assert 0.3 < overfitting_score < 0.35


def test_calculate_overfitting_score_no_degradation(sample_config):
    """Test overfitting score with no performance degradation"""
    analyzer = WalkForwardAnalyzer(sample_config)

    in_sample_results = [Mock(win_rate=60.0, total_trades=100)]
    out_of_sample_results = [Mock(win_rate=60.0, total_trades=100)]

    overfitting_score = analyzer._calculate_overfitting_score(in_sample_results, out_of_sample_results)

    assert overfitting_score == 0.0


def test_calculate_overfitting_score_improvement(sample_config):
    """Test overfitting score when out-of-sample performs better"""
    analyzer = WalkForwardAnalyzer(sample_config)

    in_sample_results = [Mock(win_rate=50.0, total_trades=100)]
    out_of_sample_results = [Mock(win_rate=70.0, total_trades=100)]

    overfitting_score = analyzer._calculate_overfitting_score(in_sample_results, out_of_sample_results)

    # Negative degradation should be clamped to 0
    assert overfitting_score == 0.0


# Test empty result creation
def test_create_empty_result(sample_config):
    """Test creation of empty walk-forward result"""
    analyzer = WalkForwardAnalyzer(sample_config)

    result = analyzer._create_empty_result()

    assert isinstance(result, WalkForwardResult)
    assert result.total_trades == 0
    assert result.total_pnl == 0.0
    assert result.win_rate == 0.0
    assert len(result.equity_curve) == 0


# Test full walk-forward analysis
@pytest.mark.asyncio
async def test_run_walkforward_analysis(sample_config, sample_multi_symbol_data):
    """Test running full walk-forward analysis"""
    analyzer = WalkForwardAnalyzer(sample_config)
    strategy = MockStrategy()
    symbols = ['BTC/USDT', 'ETH/USDT']

    # Mock the BacktestEngine.run_backtest method
    mock_trade = BacktestTrade(
        symbol='BTC/USDT',
        side='buy',
        entry_price=100.0,
        exit_price=105.0,
        quantity=1.0,
        entry_time=datetime(2024, 1, 1),
        exit_time=datetime(2024, 1, 2),
        pnl=5.0,
        pnl_percentage=5.0,
        exit_reason='take_profit',
        entry_signal_strength=0.8,
        fees=0.1
    )

    mock_backtest_result = BacktestResult(
        trades=[mock_trade],
        total_trades=1,
        winning_trades=1,
        losing_trades=0,
        total_pnl=5.0,
        total_pnl_percentage=5.0,
        win_rate=100.0,
        profit_factor=float('inf'),
        max_drawdown=0.0,
        sharpe_ratio=0.0,
        avg_win=5.0,
        avg_loss=0.0,
        max_consecutive_wins=1,
        max_consecutive_losses=0,
        equity_curve=[10000.0, 10005.0],
        timestamps=[datetime(2024, 1, 1), datetime(2024, 1, 2)]
    )

    with patch.object(BacktestEngine, 'run_backtest', new_callable=AsyncMock, return_value=mock_backtest_result):
        result = await analyzer.run_walkforward_analysis(strategy, sample_multi_symbol_data, symbols)

    assert isinstance(result, WalkForwardResult)
    assert result.total_trades > 0
    assert len(result.in_sample_results) > 0
    assert len(result.out_of_sample_results) > 0
    assert result.overfitting_score >= 0.0
    assert result.overfitting_score <= 1.0


@pytest.mark.asyncio
async def test_run_walkforward_analysis_with_ml_strategy(sample_config, sample_multi_symbol_data):
    """Test walk-forward with ML strategy that has train_model method"""
    analyzer = WalkForwardAnalyzer(sample_config)
    strategy = MockStrategy()
    symbols = ['BTC/USDT']

    mock_backtest_result = BacktestResult(
        trades=[],
        total_trades=0,
        winning_trades=0,
        losing_trades=0,
        total_pnl=0.0,
        total_pnl_percentage=0.0,
        win_rate=0.0,
        profit_factor=0.0,
        max_drawdown=0.0,
        sharpe_ratio=0.0,
        avg_win=0.0,
        avg_loss=0.0,
        max_consecutive_wins=0,
        max_consecutive_losses=0,
        equity_curve=[10000.0],
        timestamps=[datetime(2024, 1, 1)]
    )

    with patch.object(BacktestEngine, 'run_backtest', new_callable=AsyncMock, return_value=mock_backtest_result):
        result = await analyzer.run_walkforward_analysis(strategy, sample_multi_symbol_data, symbols)

    # Strategy should have been trained
    assert strategy.train_call_count > 0
    assert 'BTC/USDT' in strategy.trained_symbols


@pytest.mark.asyncio
async def test_run_walkforward_analysis_empty_data(sample_config):
    """Test walk-forward with no data"""
    analyzer = WalkForwardAnalyzer(sample_config)
    strategy = MockStrategy()

    result = await analyzer.run_walkforward_analysis(strategy, {}, [])

    assert result.total_trades == 0
    assert result.total_pnl == 0.0
    assert len(result.in_sample_results) == 0


# Test print results (just ensure no errors)
def test_print_results(sample_config, capsys):
    """Test printing walk-forward results"""
    analyzer = WalkForwardAnalyzer(sample_config)

    # Create a sample result
    result = WalkForwardResult(
        in_sample_results=[Mock(win_rate=75.0)],
        out_of_sample_results=[Mock(win_rate=60.0)],
        total_trades=100,
        winning_trades=60,
        total_pnl=1500.0,
        win_rate=60.0,
        profit_factor=2.5,
        max_drawdown=15.0,
        sharpe_ratio=1.8,
        avg_win=35.0,
        avg_loss=20.0,
        overfitting_score=0.2,
        equity_curve=[10000, 11500],
        timestamps=[datetime(2024, 1, 1), datetime(2024, 12, 31)]
    )

    analyzer.print_results(result)

    captured = capsys.readouterr()
    assert "WALK-FORWARD ANALYSIS RESULTS" in captured.out
    assert "Total Trades: 100" in captured.out
    assert "Win Rate: 60.00%" in captured.out
    assert "Overfitting Score: 0.20" in captured.out


# Test WalkForwardResult dataclass
def test_walkforward_result_creation():
    """Test creating a WalkForwardResult"""
    result = WalkForwardResult(
        in_sample_results=[],
        out_of_sample_results=[],
        total_trades=50,
        winning_trades=30,
        total_pnl=500.0,
        win_rate=60.0,
        profit_factor=2.0,
        max_drawdown=10.0,
        sharpe_ratio=1.5,
        avg_win=25.0,
        avg_loss=15.0,
        overfitting_score=0.15,
        equity_curve=[10000, 10500],
        timestamps=[datetime(2024, 1, 1), datetime(2024, 12, 31)]
    )

    assert result.total_trades == 50
    assert result.winning_trades == 30
    assert result.profit_factor == 2.0
    assert result.overfitting_score == 0.15


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

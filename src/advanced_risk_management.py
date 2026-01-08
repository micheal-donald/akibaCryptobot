import numpy as np
import pandas as pd
from typing import Dict, List, Optional, Tuple
from datetime import datetime
import logging
from dataclasses import dataclass
from scipy.stats import pearsonr

from indicators.technical_indicators import TechnicalIndicators

logger = logging.getLogger(__name__)


@dataclass
class PortfolioHeatMap:
    """Represents portfolio exposure across correlated assets"""
    timestamp: datetime
    asset_exposures: Dict[str, float]  # symbol -> exposure percentage
    correlations: Dict[Tuple[str, str], float]  # (symbol1, symbol2) -> correlation
    total_portfolio_risk: float
    heatmap_data: pd.DataFrame


@dataclass
class PositionSizingResult:
    """Result of position sizing calculation"""
    position_size: float
    risk_amount: float
    atr: float
    kelly_fraction: float
    adjusted_size: float


class AdvancedRiskManager:
    """Advanced risk management with portfolio-level controls and correlation analysis"""
    
    def __init__(self, config: Dict):
        self.config = config
        
        # Kelly Criterion parameters
        self.kelly_enabled = config.get('kelly_criterion', {}).get('enabled', False)
        self.kelly_fractions_cap = config.get('kelly_criterion', {}).get('cap', 0.25)
        
        # Correlation parameters
        self.correlation_threshold = config.get('correlation', {}).get('threshold', 0.7)
        self.max_correlated_positions = config.get('correlation', {}).get('max_positions', 2)
        
        # ATR-based position sizing
        self.atr_period = config.get('atr_position_sizing', {}).get('period', 14)
        self.atr_multiplier = config.get('atr_position_sizing', {}).get('multiplier', 2.0)
        
        # Circuit breaker parameters
        self.volatility_threshold = config.get('circuit_breaker', {}).get('volatility_threshold', 0.1)  # 10%
        self.drawdown_threshold = config.get('circuit_breaker', {}).get('drawdown_threshold', 0.05)  # 5%
        
        # Portfolio tracking
        self.portfolio_history = []
        self.asset_correlations = {}
        self.position_heatmap = {}
        
        logger.info("Advanced Risk Manager initialized")
    
    def calculate_atr_position_size(self, balance: float, entry_price: float, 
                                  symbol_data: pd.DataFrame, symbol: str) -> float:
        """
        Calculate position size based on ATR (Average True Range) volatility
        
        Args:
            balance: Current account balance
            entry_price: Current entry price
            symbol_data: Historical OHLCV data for the symbol
            symbol: Trading symbol
            
        Returns:
            Position size adjusted for volatility
        """
        try:
            if len(symbol_data) < self.atr_period:
                logger.warning(f"Insufficient data for ATR calculation for {symbol}")
                return 0.0
            
            # Calculate ATR
            atr = TechnicalIndicators.atr(
                symbol_data['high'], 
                symbol_data['low'], 
                symbol_data['close'], 
                period=self.atr_period
            ).iloc[-1]
            
            if atr <= 0:
                logger.warning(f"Invalid ATR value for {symbol}: {atr}")
                return 0.0
            
            # Calculate risk per position (default 1% of portfolio)
            risk_percent = self.config.get('position_sizing', {}).get('risk_percent', 0.01)
            risk_amount = balance * risk_percent
            
            # Calculate position size based on ATR
            # Position size = Risk Amount / (ATR Multiplier * ATR)
            position_size = risk_amount / (self.atr_multiplier * atr)
            
            logger.debug(f"ATR Position sizing for {symbol}: "
                        f"ATR={atr:.6f}, Risk Amount=${risk_amount:.2f}, "
                        f"Position Size={position_size:.6f}")
            
            return position_size
            
        except Exception as e:
            logger.error(f"Error calculating ATR position size for {symbol}: {e}")
            return 0.0
    
    def calculate_kelly_position_size(self, win_rate: float, win_loss_ratio: float, 
                                    base_position_size: float) -> float:
        """
        Calculate optimal position size using Kelly Criterion
        
        Args:
            win_rate: Historical win rate (0-1)
            win_loss_ratio: Average win / Average loss
            base_position_size: Base position size from other calculations
            
        Returns:
            Adjusted position size based on Kelly Criterion
        """
        try:
            if not self.kelly_enabled:
                return base_position_size
            
            # Kelly Criterion formula: K = W - [(1 - W) / R]
            # Where:
            # K = Kelly fraction
            # W = Win probability
            # R = Win/Loss ratio
            kelly_fraction = win_rate - ((1 - win_rate) / win_loss_ratio) if win_loss_ratio > 0 else 0
            
            # Apply a fractional Kelly (e.g., half Kelly) to reduce volatility
            fractional_kelly = kelly_fraction * 0.5
            
            # Cap the Kelly fraction to prevent over-betting
            fractional_kelly = min(fractional_kelly, self.kelly_fractions_cap)
            
            # Ensure we don't go negative
            fractional_kelly = max(fractional_kelly, 0.0)
            
            # Adjust position size based on Kelly fraction
            adjusted_size = base_position_size * (1 + fractional_kelly)
            
            logger.debug(f"Kelly Criterion adjustment: "
                        f"Win Rate={win_rate:.2f}, W/L Ratio={win_loss_ratio:.2f}, "
                        f"Kelly Fraction={fractional_kelly:.4f}, "
                        f"Adjusted Size={adjusted_size:.6f}")
            
            return adjusted_size
            
        except Exception as e:
            logger.error(f"Error calculating Kelly position size: {e}")
            return base_position_size
    
    def calculate_position_sizing(self, balance: float, entry_price: float,
                                symbol_data: pd.DataFrame, symbol: str,
                                win_rate: float = 0.5, win_loss_ratio: float = 2.0) -> PositionSizingResult:
        """
        Calculate comprehensive position sizing using multiple methods
        
        Args:
            balance: Current account balance
            entry_price: Current entry price
            symbol_data: Historical OHLCV data for the symbol
            symbol: Trading symbol
            win_rate: Strategy's historical win rate
            win_loss_ratio: Strategy's historical win/loss ratio
            
        Returns:
            PositionSizingResult with all calculations
        """
        # Calculate ATR-based position size
        atr_position_size = self.calculate_atr_position_size(balance, entry_price, symbol_data, symbol)
        
        # Calculate ATR value
        atr = 0.0
        if len(symbol_data) >= self.atr_period:
            atr = TechnicalIndicators.atr(
                symbol_data['high'], 
                symbol_data['low'], 
                symbol_data['close'], 
                period=self.atr_period
            ).iloc[-1]
        
        # Calculate risk amount
        risk_percent = self.config.get('position_sizing', {}).get('risk_percent', 0.01)
        risk_amount = balance * risk_percent
        
        # Calculate Kelly-adjusted size
        kelly_size = self.calculate_kelly_position_size(win_rate, win_loss_ratio, atr_position_size)
        
        # Final position size (use the more conservative of ATR and Kelly)
        final_position_size = min(atr_position_size, kelly_size)
        
        return PositionSizingResult(
            position_size=final_position_size,
            risk_amount=risk_amount,
            atr=atr,
            kelly_fraction=kelly_size / atr_position_size if atr_position_size > 0 else 0,
            adjusted_size=final_position_size
        )
    
    def calculate_correlations(self, price_data: Dict[str, pd.DataFrame]) -> Dict[Tuple[str, str], float]:
        """
        Calculate correlations between all pairs of assets
        
        Args:
            price_data: Dictionary mapping symbols to OHLCV data
            
        Returns:
            Dictionary of correlations (symbol1, symbol2) -> correlation
        """
        try:
            if len(price_data) < 2:
                return {}
            
            # Extract close prices for each symbol
            close_prices = pd.DataFrame()
            for symbol, data in price_data.items():
                if not data.empty:
                    close_prices[symbol] = data['close']
            
            if close_prices.empty or len(close_prices.columns) < 2:
                return {}
            
            # Calculate correlation matrix
            correlation_matrix = close_prices.corr(method='pearson')
            
            # Convert to dictionary format
            correlations = {}
            symbols = correlation_matrix.columns.tolist()
            
            for i, symbol1 in enumerate(symbols):
                for j, symbol2 in enumerate(symbols):
                    if i < j:  # Only store each pair once
                        correlation = correlation_matrix.loc[symbol1, symbol2]
                        correlations[(symbol1, symbol2)] = correlation
            
            self.asset_correlations = correlations
            return correlations
            
        except Exception as e:
            logger.error(f"Error calculating correlations: {e}")
            return {}
    
    def generate_portfolio_heatmap(self, positions: Dict[str, Dict], 
                                 price_data: Dict[str, pd.DataFrame], 
                                 balance: float) -> PortfolioHeatMap:
        """
        Generate portfolio heatmap showing exposure across correlated assets
        
        Args:
            positions: Current positions dictionary
            price_data: Dictionary mapping symbols to OHLCV data
            balance: Current account balance
            
        Returns:
            PortfolioHeatMap with exposure and correlation data
        """
        try:
            # Calculate correlations
            correlations = self.calculate_correlations(price_data)
            
            # Calculate asset exposures
            asset_exposures = {}
            total_exposure = 0.0
            
            for symbol, position in positions.items():
                exposure = position['amount'] * position['entry_price']
                asset_exposures[symbol] = exposure
                total_exposure += exposure
            
            # Calculate exposure percentages
            exposure_percentages = {}
            for symbol, exposure in asset_exposures.items():
                exposure_percentages[symbol] = (exposure / balance) * 100 if balance > 0 else 0
            
            # Create heatmap data
            symbols = list(asset_exposures.keys())
            heatmap_data = pd.DataFrame(index=symbols, columns=symbols)
            
            # Fill diagonal with exposure percentages
            for symbol in symbols:
                heatmap_data.loc[symbol, symbol] = exposure_percentages.get(symbol, 0)
            
            # Fill off-diagonal with correlations
            for i, symbol1 in enumerate(symbols):
                for j, symbol2 in enumerate(symbols):
                    if i != j:
                        if (symbol1, symbol2) in correlations:
                            heatmap_data.loc[symbol1, symbol2] = correlations[(symbol1, symbol2)]
                        elif (symbol2, symbol1) in correlations:
                            heatmap_data.loc[symbol1, symbol2] = correlations[(symbol2, symbol1)]
                        else:
                            heatmap_data.loc[symbol1, symbol2] = 0
            
            portfolio_risk = (total_exposure / balance) * 100 if balance > 0 else 0
            
            heatmap = PortfolioHeatMap(
                timestamp=datetime.now(),
                asset_exposures=exposure_percentages,
                correlations=correlations,
                total_portfolio_risk=portfolio_risk,
                heatmap_data=heatmap_data
            )
            
            self.position_heatmap = heatmap
            return heatmap
            
        except Exception as e:
            logger.error(f"Error generating portfolio heatmap: {e}")
            return PortfolioHeatMap(
                timestamp=datetime.now(),
                asset_exposures={},
                correlations={},
                total_portfolio_risk=0.0,
                heatmap_data=pd.DataFrame()
            )
    
    def check_correlation_diversification(self, symbol: str, 
                                        current_positions: Dict[str, Dict]) -> bool:
        """
        Check if adding a new position would violate correlation diversification rules
        
        Args:
            symbol: Symbol to check
            current_positions: Current positions dictionary
            
        Returns:
            True if position can be added, False otherwise
        """
        try:
            # If no positions, can always add
            if not current_positions:
                return True
            
            # Count positions with high correlation to the new symbol
            correlated_positions = 0
            
            for pos_symbol, position in current_positions.items():
                # Check correlation between symbols
                correlation_key = (symbol, pos_symbol) if symbol < pos_symbol else (pos_symbol, symbol)
                
                if correlation_key in self.asset_correlations:
                    correlation = self.asset_correlations[correlation_key]
                    if abs(correlation) > self.correlation_threshold:
                        correlated_positions += 1
            
            # Check if adding this position would exceed the limit
            if correlated_positions >= self.max_correlated_positions:
                logger.warning(f"Correlation diversification check failed for {symbol}. "
                             f"Too many correlated positions: {correlated_positions}")
                return False
            
            return True
            
        except Exception as e:
            logger.error(f"Error checking correlation diversification for {symbol}: {e}")
            # In case of error, allow the position (fail-safe approach)
            return True
    
    def check_circuit_breakers(self, price_data: Dict[str, pd.DataFrame], 
                             portfolio_value_history: List[float]) -> Tuple[bool, str]:
        """
        Check circuit breakers for extreme market conditions
        
        Args:
            price_data: Dictionary mapping symbols to OHLCV data
            portfolio_value_history: Historical portfolio values
            
        Returns:
            Tuple of (should_stop_trading, reason)
        """
        try:
            # Check portfolio drawdown
            if len(portfolio_value_history) > 1:
                current_value = portfolio_value_history[-1]
                peak_value = max(portfolio_value_history)
                
                if peak_value > 0:
                    drawdown = (peak_value - current_value) / peak_value
                    if drawdown > self.drawdown_threshold:
                        return True, f"Portfolio drawdown {drawdown:.2%} exceeds threshold {self.drawdown_threshold:.2%}"
            
            # Check asset volatility
            for symbol, data in price_data.items():
                if len(data) < 2:
                    continue
                
                # Calculate recent returns
                close_prices = data['close'].tail(30)  # Last 30 periods
                returns = close_prices.pct_change().dropna()
                
                if len(returns) > 1:
                    volatility = returns.std() * np.sqrt(252)  # Annualized volatility
                    if volatility > self.volatility_threshold:
                        return True, f"Asset {symbol} volatility {volatility:.2%} exceeds threshold {self.volatility_threshold:.2%}"
            
            return False, "No circuit breaker conditions met"
            
        except Exception as e:
            logger.error(f"Error checking circuit breakers: {e}")
            return False, "Error in circuit breaker check"
    
    def get_risk_report(self) -> Dict:
        """
        Generate a comprehensive risk report
        
        Returns:
            Dictionary with risk metrics
        """
        return {
            'timestamp': datetime.now().isoformat(),
            'portfolio_heatmap': {
                'total_risk': getattr(self.position_heatmap, 'total_portfolio_risk', 0),
                'asset_exposures': getattr(self.position_heatmap, 'asset_exposures', {}),
                'correlations': getattr(self.position_heatmap, 'correlations', {})
            },
            'correlation_parameters': {
                'threshold': self.correlation_threshold,
                'max_correlated_positions': self.max_correlated_positions
            },
            'position_sizing_parameters': {
                'atr_period': self.atr_period,
                'atr_multiplier': self.atr_multiplier,
                'kelly_enabled': self.kelly_enabled,
                'kelly_cap': self.kelly_fractions_cap
            },
            'circuit_breaker_parameters': {
                'volatility_threshold': self.volatility_threshold,
                'drawdown_threshold': self.drawdown_threshold
            }
        }
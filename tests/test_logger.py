import pytest
import logging
import os
import sys
from unittest.mock import Mock, patch, MagicMock

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bot_logging.logger import setup_logging


class TestLogger:
    """Test cases for logger module"""

    @patch("logging.handlers.RotatingFileHandler")
    def test_setup_logger(self, mock_fh):
        """Test logger setup"""
        mock_fh.return_value = MagicMock(spec=logging.Handler)

        logger = setup_logging({"level": "INFO"})
        assert logger.name == "akiba_crypto_bot"
        assert logger.level == logging.INFO
        assert len(logger.handlers) >= 1

        # Clean up
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

    @patch("logging.handlers.RotatingFileHandler")
    def test_get_logger(self, mock_fh):
        """Test getting logger instance returns same object"""
        mock_fh.return_value = MagicMock(spec=logging.Handler)

        logger1 = setup_logging({"level": "INFO"})
        logger2 = setup_logging({"level": "INFO"})

        # Should return the same logger instance (Python logger caching by name)
        assert logger1 is logger2
        assert logger1.name == "akiba_crypto_bot"

        # Clean up
        for handler in logger1.handlers[:]:
            logger1.removeHandler(handler)

    @patch("logging.handlers.RotatingFileHandler")
    def test_logger_handlers(self, mock_fh):
        """Test that logger has a console handler"""
        mock_fh.return_value = MagicMock(spec=logging.Handler)

        logger = setup_logging({"level": "INFO"})

        # Should have at least a StreamHandler (console)
        stream_handlers = [
            h
            for h in logger.handlers
            if isinstance(h, logging.StreamHandler)
            and not isinstance(h, logging.FileHandler)
        ]
        assert len(stream_handlers) >= 1

        # Clean up
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

    @patch("logging.handlers.RotatingFileHandler")
    def test_logger_formatting(self, mock_fh):
        """Test logger message formatting"""
        mock_fh.return_value = MagicMock(spec=logging.Handler)

        logger = setup_logging({"level": "INFO"})

        # Find a StreamHandler with a formatter
        formatter = None
        for handler in logger.handlers:
            if isinstance(handler, logging.StreamHandler) and handler.formatter:
                formatter = handler.formatter
                break

        assert formatter is not None
        assert "%(asctime)s" in formatter._fmt
        assert "%(name)s" in formatter._fmt
        assert "%(levelname)s" in formatter._fmt
        assert "%(message)s" in formatter._fmt

        # Clean up
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

    @patch("logging.handlers.RotatingFileHandler")
    def test_logger_levels(self, mock_fh):
        """Test logger levels"""
        mock_fh.return_value = MagicMock(spec=logging.Handler)

        logger = setup_logging({"level": "INFO"})

        assert logger.isEnabledFor(logging.INFO) == True
        assert logger.isEnabledFor(logging.DEBUG) == False
        assert logger.isEnabledFor(logging.WARNING) == True
        assert logger.isEnabledFor(logging.ERROR) == True

        # Clean up
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)

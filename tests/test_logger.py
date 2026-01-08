import pytest
import asyncio
import logging
import os
import sys
from unittest.mock import Mock, patch, MagicMock

# Add src to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from bot_logging.logger import (
    setup_logging as setup_logger,
    setup_logging as get_logger,
)


class TestLogger:
    """Test cases for logger module"""

    def test_setup_logger(self):
        """Test logger setup"""
        # Test creating a logger with default parameters
        logger = setup_logger("test_logger")
        assert logger.name == "test_logger"
        assert logger.level == logging.INFO

        # Test creating a logger with custom level
        debug_logger = setup_logger("debug_logger", level=logging.DEBUG)
        assert debug_logger.level == logging.DEBUG

        # Test that logger has the expected handlers
        assert len(logger.handlers) >= 1

        # Clean up by removing handlers
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

    def test_get_logger(self):
        """Test getting logger instance"""
        # Test getting a logger by name
        logger1 = get_logger("test_logger")
        logger2 = get_logger("test_logger")

        # Should return the same logger instance
        assert logger1 is logger2
        assert logger1.name == "test_logger"

    @patch("logger.logging.FileHandler")
    @patch("logger.logging.StreamHandler")
    def test_logger_handlers(self, mock_stream_handler, mock_file_handler):
        """Test that logger has correct handlers"""
        # Mock the handlers
        mock_stream_instance = Mock()
        mock_file_instance = Mock()
        mock_stream_handler.return_value = mock_stream_instance
        mock_file_handler.return_value = mock_file_instance

        # Create logger
        logger = setup_logger("handler_test_logger")

        # Check that handlers were created
        mock_stream_handler.assert_called_once()
        mock_file_handler.assert_called_once()

        # Check that handlers were added to logger
        assert mock_stream_instance in logger.handlers
        assert mock_file_instance in logger.handlers

        # Clean up
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

    def test_logger_formatting(self):
        """Test logger message formatting"""
        # Create logger
        logger = setup_logger("format_test_logger")

        # Find the formatter
        formatter = None
        for handler in logger.handlers:
            if hasattr(handler, "formatter") and handler.formatter:
                formatter = handler.formatter
                break

        # Check that formatter exists and has expected format
        assert formatter is not None
        assert "%(asctime)s" in formatter._fmt
        assert "%(name)s" in formatter._fmt
        assert "%(levelname)s" in formatter._fmt
        assert "%(message)s" in formatter._fmt

        # Clean up
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

    def test_logger_levels(self):
        """Test logger levels"""
        logger = setup_logger("level_test_logger")

        # Test different log levels
        assert logger.isEnabledFor(logging.INFO) == True
        assert logger.isEnabledFor(logging.DEBUG) == False  # Default is INFO
        assert logger.isEnabledFor(logging.WARNING) == True
        assert logger.isEnabledFor(logging.ERROR) == True

        # Clean up
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()

# Makefile for akibaCryptobot

# Variables
PYTHON := python3
PIP := pip3
TEST_DIR := tests
SRC_DIR := src

# Default target
.PHONY: help
help:
	@echo "akibaCryptobot Makefile"
	@echo "======================"
	@echo "Available targets:"
	@echo "  install      Install dependencies"
	@echo "  install-dev  Install development dependencies"
	@echo "  test         Run tests"
	@echo "  test-cov     Run tests with coverage"
	@echo "  lint         Run code linting"
	@echo "  format       Format code with black"
	@echo "  type-check   Run type checking with mypy"
	@echo "  clean        Clean Python cache files"
	@echo "  run          Run the bot"
	@echo "  run-testnet  Run the bot in testnet mode"
	@echo "  run-strategy Run with specific strategy"
	@echo "  run-freqai-ml Run with FreqAI ML strategy"
	@echo "  run-freqai-example Run with FreqAI example strategy"
	@echo "  freqai-demo  Run FreqAI demo"
	@echo "  docs         Generate documentation"
	@echo "  help         Show this help message"

# Install dependencies
.PHONY: install
install:
	$(PIP) install -r requirements.txt

# Install development dependencies
.PHONY: install-dev
install-dev:
	$(PIP) install -r requirements.txt
	$(PIP) install -r requirements-dev.txt

# Run tests
.PHONY: test
test:
	$(PYTHON) -m pytest $(TEST_DIR) -v

# Run tests with coverage
.PHONY: test-cov
test-cov:
	$(PYTHON) -m pytest $(TEST_DIR) --cov=$(SRC_DIR) --cov-report=html --cov-report=term

# Run code linting
.PHONY: lint
lint:
	flake8 $(SRC_DIR) $(TEST_DIR)

# Format code with black
.PHONY: format
format:
	black $(SRC_DIR) $(TEST_DIR)

# Run type checking with mypy
.PHONY: type-check
type-check:
	mypy $(SRC_DIR) $(TEST_DIR)

# Clean Python cache files
.PHONY: clean
clean:
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name ".pytest_cache" -delete
	rm -rf .mypy_cache
	rm -rf htmlcov
	rm -rf .coverage

# Run the bot
.PHONY: run
run:
	$(PYTHON) start_bot.py

# Run the bot in testnet mode
.PHONY: run-testnet
run-testnet:
	$(PYTHON) start_bot.py --testnet

# Run FreqAI demo
.PHONY: freqai-demo
freqai-demo:
	$(PYTHON) examples/freqai_demo.py

# Run with specific strategy
.PHONY: run-strategy
run-strategy:
	$(PYTHON) start_bot.py --strategy $(STRATEGY)

# Run with FreqAI ML strategy
.PHONY: run-freqai-ml
run-freqai-ml:
	$(PYTHON) start_bot.py --strategy freqai_ml_freqai

# Run with FreqAI example strategy
.PHONY: run-freqai-example
run-freqai-example:
	$(PYTHON) start_bot.py --strategy freqai_example_freqai

# Generate documentation
.PHONY: docs
docs:
	@echo "Generating documentation..."
	# Add documentation generation commands here if needed

# Run all checks
.PHONY: check
check: lint type-check test

# Run all checks with coverage
.PHONY: check-cov
check-cov: lint type-check test-cov

import pytest
import asyncio
import sys
import os

# Add src to path for imports
sys.path.append(os.path.join(os.path.dirname(__file__), 'src'))

# Add a marker for async tests
def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: mark test as async")

# Fixture for event loop
@pytest.fixture(scope="session")
def event_loop():
    """Create an instance of the default event loop for each test case."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
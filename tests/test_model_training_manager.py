from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from operations.model_training_manager import ModelTrainingManager
from persistence.trade_store import TradeStore


class _FakeStrategy:
    def load_models(self, models_dir: str) -> int:
        return len(list(Path(models_dir).glob("*_freqai.joblib")))


class _FakeStrategyManager:
    def __init__(self):
        self._active_strategy = _FakeStrategy()

    def get_active_strategy(self):
        return self._active_strategy


class _FakeBot:
    def __init__(self, trade_store: TradeStore, repo_root: Path):
        self.trade_store = trade_store
        self._background_tasks: set = set()
        self.config = {
            "exchange": {"sandbox": True},
            "trading": {"pairs": ["BTC/USDT", "ETH/USDT"]},
        }
        self.trading_paused = True
        self.strategy_manager = _FakeStrategyManager()
        self.repo_root = repo_root
        self.reload_calls = 0

    def reload_freqai_models(self, models_dir: str | None = None) -> int:
        self.reload_calls += 1
        target_dir = Path(models_dir or self.repo_root / "models")
        return len(list(target_dir.glob("*_freqai.joblib")))


@pytest_asyncio.fixture
async def training_store(tmp_path):
    store = TradeStore(str(tmp_path / "training.db"))
    await store.initialize()
    yield store
    await store.close()


async def _create_staged_models(staging_dir: Path, pairs: tuple[str, ...]) -> None:
    staging_dir.mkdir(parents=True, exist_ok=True)
    for pair in pairs:
        (staging_dir / f"{pair.replace('/', '_')}_freqai.joblib").write_bytes(b"model")


@pytest.mark.asyncio
async def test_training_manager_runs_successfully(training_store, tmp_path):
    bot = _FakeBot(training_store, tmp_path)
    manager = ModelTrainingManager(
        bot, config_path="config/config.json", repo_root=tmp_path
    )

    async def fake_execute(record, staging_dir):
        await _create_staged_models(staging_dir, record.pairs)
        return "ok"

    manager._execute_training_subprocess = fake_execute  # type: ignore[method-assign]

    record = await manager.enqueue_training_job(
        requested_by="api",
        pairs=["BTC/USDT", "ETH/USDT"],
        candles=1200,
        timeframe="1h",
    )
    await manager._runner_task

    snapshot = await training_store.get_training_job_status()
    assert record.status == "queued"
    assert snapshot.current_job is None
    assert snapshot.last_job is not None
    assert snapshot.last_job.status == "succeeded"
    assert snapshot.last_job.promoted_model_paths == (
        "models/BTC_USDT_freqai.joblib",
        "models/ETH_USDT_freqai.joblib",
    )
    assert snapshot.last_job.reload_status == "reloaded"
    assert bot.reload_calls == 1


@pytest.mark.asyncio
async def test_training_manager_marks_timeouts_failed(training_store, tmp_path):
    bot = _FakeBot(training_store, tmp_path)
    manager = ModelTrainingManager(
        bot, config_path="config/config.json", repo_root=tmp_path
    )

    async def fake_execute(record, staging_dir):
        raise TimeoutError("training_subprocess_timeout")

    manager._execute_training_subprocess = fake_execute  # type: ignore[method-assign]

    await manager.enqueue_training_job(
        requested_by="telegram",
        pairs=["BTC/USDT"],
        candles=800,
        timeframe="4h",
    )
    await manager._runner_task

    snapshot = await training_store.get_training_job_status()
    assert snapshot.last_job is not None
    assert snapshot.last_job.status == "failed"
    assert snapshot.last_job.error == "training_subprocess_timeout"
    assert bot.reload_calls == 0


@pytest.mark.asyncio
async def test_training_manager_restores_backup_on_reload_failure(
    training_store, tmp_path
):
    bot = _FakeBot(training_store, tmp_path)
    models_dir = tmp_path / "models"
    models_dir.mkdir(parents=True, exist_ok=True)
    original_model = models_dir / "BTC_USDT_freqai.joblib"
    original_model.write_bytes(b"original")

    def failing_reload(models_dir: str | None = None) -> int:
        raise RuntimeError("reload_failed")

    bot.reload_freqai_models = failing_reload  # type: ignore[assignment]
    manager = ModelTrainingManager(
        bot, config_path="config/config.json", repo_root=tmp_path
    )

    async def fake_execute(record, staging_dir):
        await _create_staged_models(staging_dir, ("BTC/USDT",))
        return "ok"

    manager._execute_training_subprocess = fake_execute  # type: ignore[method-assign]

    await manager.enqueue_training_job(
        requested_by="api",
        pairs=["BTC/USDT"],
        candles=1000,
        timeframe="1h",
    )
    await manager._runner_task

    snapshot = await training_store.get_training_job_status()
    assert snapshot.last_job is not None
    assert snapshot.last_job.status == "failed"
    assert snapshot.last_job.error == "reload_failed"
    assert original_model.read_bytes() == b"original"


@pytest.mark.asyncio
async def test_training_manager_recovers_interrupted_jobs(training_store, tmp_path):
    bot = _FakeBot(training_store, tmp_path)
    manager = ModelTrainingManager(
        bot, config_path="config/config.json", repo_root=tmp_path
    )

    await training_store.enqueue_training_job(
        {
            "pairs": ["BTC/USDT"],
            "candles": 1000,
            "timeframe": "1h",
            "requested_by": "system",
        }
    )

    await manager.initialize()

    snapshot = await training_store.get_training_job_status()
    assert snapshot.current_job is None
    assert snapshot.last_job is not None
    assert snapshot.last_job.status == "failed"
    assert snapshot.last_job.error == "training_interrupted_by_restart"

"""Background training manager for server-side FreqAI model refresh."""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
from pathlib import Path
from typing import Iterable

from operations.training_jobs import TrainingJobRecord, TrainingJobRequest


class ModelTrainingManager:
    """Owns queued/running model training jobs for the bot process."""

    TRAINING_TIMEOUT_SECONDS = 45 * 60

    def __init__(
        self,
        bot,
        *,
        config_path: str,
        repo_root: str | Path | None = None,
    ) -> None:
        self.bot = bot
        self.trade_store = bot.trade_store
        self.config_path = config_path
        self.repo_root = Path(repo_root or Path(__file__).resolve().parents[2])
        self.models_dir = self.repo_root / "models"
        self.staging_root = self.repo_root / "models_staging"
        self.backup_root = self.repo_root / "models_backups"
        self._runner_lock = asyncio.Lock()
        self._runner_task: asyncio.Task | None = None

    async def initialize(self) -> None:
        """Mark any interrupted queued/running jobs as failed after restart."""

        active_job = await self.trade_store.get_active_training_job()
        if active_job is None:
            return
        await self.trade_store.mark_training_job_failed(
            active_job.job_id,
            error="training_interrupted_by_restart",
            result_summary="Training job did not finish before process restart.",
            reload_status="abandoned",
        )

    async def get_training_job_status(self):
        return await self.trade_store.get_training_job_status()

    async def can_start_training(self) -> tuple[bool, str | None]:
        blocker = await self.get_start_blocker()
        return blocker is None, blocker

    async def get_start_blocker(self) -> str | None:
        active_job = await self.trade_store.get_active_training_job()
        if active_job is not None:
            return "training_job_already_active"
        if not self.bot.config["exchange"].get("sandbox", True):
            return "training_not_allowed_in_live"
        if not getattr(self.bot, "trading_paused", False):
            return "trading_must_be_paused"
        if not self._freqai_available():
            return "freqai_not_available"
        return None

    async def enqueue_training_job(
        self,
        *,
        requested_by: str = "system",
        pairs: Iterable[str] | None = None,
        candles: int = 1000,
        timeframe: str = "1h",
    ) -> TrainingJobRecord:
        request = TrainingJobRequest(
            pairs=tuple(pairs or ()),
            candles=candles,
            timeframe=timeframe,
            requested_by=requested_by,
        )

        blocker = await self.get_start_blocker()
        if blocker == "training_job_already_active":
            return await self.trade_store.enqueue_training_job(request)
        if blocker is not None:
            raise ValueError(blocker)

        record = await self.trade_store.enqueue_training_job(request)
        if record.status == "queued":
            self._start_runner(record.job_id)
        return record

    def _start_runner(self, job_id: str) -> None:
        task = asyncio.create_task(self._run_training_job(job_id))
        self._runner_task = task
        self.bot._background_tasks.add(task)
        task.add_done_callback(self.bot._background_tasks.discard)

    async def _run_training_job(self, job_id: str) -> None:
        async with self._runner_lock:
            record = await self.trade_store.mark_training_job_running(job_id)
            if record is None:
                return

            staging_dir = self.staging_root / record.job_id
            promoted_paths: tuple[str, ...] = ()
            backup_dir: Path | None = None
            try:
                await self._execute_training_subprocess(record, staging_dir)
                await self.trade_store.update_training_job(
                    record.job_id,
                    progress=0.75,
                    result_summary="Training completed, validating staged models.",
                    reload_status="validating",
                )
                promoted_paths = self._validate_staged_models(record, staging_dir)
                backup_dir = self._promote_models(record, staging_dir)
                await self.trade_store.update_training_job(
                    record.job_id,
                    progress=0.9,
                    result_summary="Reloading staged models into the active strategy.",
                    reload_status="reloading",
                )
                loaded = self.bot.reload_freqai_models()
                await self.trade_store.mark_training_job_succeeded(
                    record.job_id,
                    promoted_model_paths=promoted_paths,
                    result_summary=f"Training finished successfully ({loaded} model(s) reloaded).",
                    reload_status="reloaded",
                )
            except Exception as exc:
                if backup_dir is not None:
                    self._restore_backup(backup_dir)
                await self.trade_store.mark_training_job_failed(
                    record.job_id,
                    error=str(exc),
                    result_summary="Training job failed.",
                    reload_status="failed",
                )
            finally:
                shutil.rmtree(staging_dir, ignore_errors=True)

    async def _execute_training_subprocess(
        self,
        record: TrainingJobRecord,
        staging_dir: Path,
    ) -> str:
        staging_dir.mkdir(parents=True, exist_ok=True)
        command = self._build_command(record, staging_dir)
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(self.repo_root),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=self.TRAINING_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            raise TimeoutError("training_subprocess_timeout") from None

        if process.returncode != 0:
            stderr_text = (stderr or b"").decode().strip()
            stdout_text = (stdout or b"").decode().strip()
            detail = stderr_text or stdout_text or "unknown training error"
            raise RuntimeError(f"training_subprocess_failed: {detail}")

        return (stdout or b"").decode()

    def _build_command(self, record: TrainingJobRecord, staging_dir: Path) -> list[str]:
        base_command = [
            sys.executable,
            "scripts/train_models.py",
            "--config",
            self.config_path,
            "--candles",
            str(record.candles),
            "--timeframe",
            record.timeframe,
            "--output-dir",
            os.fspath(staging_dir),
            "--n-jobs",
            "1",
        ]
        if record.pairs:
            base_command.extend(["--pairs", *record.pairs])

        if os.name == "posix" and shutil.which("nice"):
            return ["nice", "-n", "10", *base_command]
        return base_command

    def _validate_staged_models(
        self,
        record: TrainingJobRecord,
        staging_dir: Path,
    ) -> tuple[str, ...]:
        expected_pairs = record.pairs or tuple(
            self.bot.config.get("trading", {}).get("pairs", ())
        )
        if not expected_pairs:
            raise RuntimeError("no_training_pairs_configured")

        promoted_paths: list[str] = []
        for pair in expected_pairs:
            safe_pair = pair.replace("/", "_")
            staged_path = staging_dir / f"{safe_pair}_freqai.joblib"
            if not staged_path.exists():
                raise RuntimeError(f"missing_staged_model:{pair}")
            promoted_paths.append(f"models/{staged_path.name}")
        return tuple(promoted_paths)

    def _promote_models(
        self,
        record: TrainingJobRecord,
        staging_dir: Path,
    ) -> Path | None:
        self.backup_root.mkdir(parents=True, exist_ok=True)
        promotion_dir = self.backup_root / f"{record.job_id}-promotion"
        backup_dir = self.backup_root / f"{record.job_id}-backup"
        shutil.rmtree(promotion_dir, ignore_errors=True)
        shutil.rmtree(backup_dir, ignore_errors=True)
        shutil.copytree(staging_dir, promotion_dir)

        if self.models_dir.exists():
            os.replace(self.models_dir, backup_dir)
        else:
            backup_dir = None

        try:
            os.replace(promotion_dir, self.models_dir)
        except Exception:
            if (
                backup_dir is not None
                and backup_dir.exists()
                and not self.models_dir.exists()
            ):
                os.replace(backup_dir, self.models_dir)
            raise
        return backup_dir

    def _restore_backup(self, backup_dir: Path) -> None:
        if self.models_dir.exists():
            shutil.rmtree(self.models_dir, ignore_errors=True)
        if backup_dir.exists():
            os.replace(backup_dir, self.models_dir)

    def _freqai_available(self) -> bool:
        active_strategy = self.bot.strategy_manager.get_active_strategy()
        if active_strategy is None:
            return False
        return hasattr(active_strategy, "load_models")

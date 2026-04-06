from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies import get_bot
from api.routes import model
from operations.training_jobs import (
    TRAINING_JOB_QUEUED,
    TrainingJobRecord,
    TrainingJobSnapshot,
)


class FakeModelBot:
    def __init__(
        self,
        *,
        model_snapshot: dict | None = None,
        training_snapshot: TrainingJobSnapshot | None = None,
        can_start_result: tuple[bool, str | None] = (True, None),
        enqueue_result: TrainingJobRecord | None = None,
    ):
        self._model_snapshot = model_snapshot or {
            "freqai_active": True,
            "models": [{"name": "freqai", "trained": True}],
            "last_success_at": "2026-04-06T10:00:00",
            "reload_status": "reloaded",
            "current_job_id": None,
        }
        self._training_snapshot = training_snapshot or TrainingJobSnapshot(
            current_job=None,
            last_job=None,
            can_start_training=True,
            active_job_count=0,
        )
        self._can_start_result = can_start_result
        self._enqueue_result = enqueue_result or TrainingJobRecord(
            job_id="job-1",
            status=TRAINING_JOB_QUEUED,
            requested_by="api",
            pairs=("BTC/USDT",),
            candles=1000,
            timeframe="1h",
        )

    async def get_model_status_info(self):
        return self._model_snapshot

    async def get_training_job_status(self):
        return self._training_snapshot

    async def can_start_training(self):
        return self._can_start_result

    async def enqueue_training_job(self, **kwargs):
        return self._enqueue_result


def build_client(bot: FakeModelBot) -> TestClient:
    app = FastAPI()
    app.include_router(model.router, prefix="/api")
    app.dependency_overrides[get_bot] = lambda: bot
    return TestClient(app)


def test_get_model_status_includes_extended_training_fields():
    client = build_client(FakeModelBot())
    response = client.get("/api/model/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["freqai_active"] is True
    assert payload["last_success_at"] == "2026-04-06T10:00:00"
    assert payload["reload_status"] == "reloaded"
    assert payload["current_job_id"] is None


def test_get_model_training_status_includes_start_blocker():
    current_job = TrainingJobRecord(
        job_id="job-2",
        status="running",
        requested_by="api",
        pairs=("BTC/USDT",),
        candles=900,
        timeframe="30m",
        progress=55.0,
    )
    snapshot = TrainingJobSnapshot(
        current_job=current_job,
        last_job=current_job,
        can_start_training=False,
        active_job_count=1,
    )
    client = build_client(
        FakeModelBot(
            training_snapshot=snapshot,
            can_start_result=(False, "training_job_already_active"),
        )
    )

    response = client.get("/api/model/train/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["current_job"]["job_id"] == "job-2"
    assert payload["can_start_training"] is False
    assert payload["start_blocker"] == "training_job_already_active"


def test_post_train_rejects_missing_operator_token(monkeypatch):
    monkeypatch.delenv("AKIBA_OPERATOR_TOKEN", raising=False)
    client = build_client(FakeModelBot())

    response = client.post("/api/model/train", json={})

    assert response.status_code == 503
    assert response.json()["detail"] == "operator_token_not_configured"


def test_post_train_rejects_invalid_operator_token(monkeypatch):
    monkeypatch.setenv("AKIBA_OPERATOR_TOKEN", "secret-token")
    client = build_client(FakeModelBot())

    response = client.post(
        "/api/model/train",
        json={},
        headers={"X-Operator-Token": "wrong-token"},
    )

    assert response.status_code == 403
    assert response.json()["detail"] == "operator_token_invalid"


def test_post_train_rejects_live_mode(monkeypatch):
    monkeypatch.setenv("AKIBA_OPERATOR_TOKEN", "secret-token")
    client = build_client(
        FakeModelBot(can_start_result=(False, "training_not_allowed_in_live"))
    )

    response = client.post(
        "/api/model/train",
        json={},
        headers={"X-Operator-Token": "secret-token"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "training_not_allowed_in_live"


def test_post_train_rejects_unpaused_trading(monkeypatch):
    monkeypatch.setenv("AKIBA_OPERATOR_TOKEN", "secret-token")
    client = build_client(
        FakeModelBot(can_start_result=(False, "trading_must_be_paused"))
    )

    response = client.post(
        "/api/model/train",
        json={},
        headers={"X-Operator-Token": "secret-token"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "trading_must_be_paused"


def test_post_train_rejects_duplicate_active_job(monkeypatch):
    monkeypatch.setenv("AKIBA_OPERATOR_TOKEN", "secret-token")
    client = build_client(
        FakeModelBot(can_start_result=(False, "training_job_already_active"))
    )

    response = client.post(
        "/api/model/train",
        json={},
        headers={"X-Operator-Token": "secret-token"},
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "training_job_already_active"


def test_post_train_returns_queued_job(monkeypatch):
    monkeypatch.setenv("AKIBA_OPERATOR_TOKEN", "secret-token")
    queued_job = TrainingJobRecord(
        job_id="job-9",
        status=TRAINING_JOB_QUEUED,
        requested_by="api",
        pairs=("BTC/USDT", "ETH/USDT"),
        candles=1500,
        timeframe="4h",
    )
    client = build_client(FakeModelBot(enqueue_result=queued_job))

    response = client.post(
        "/api/model/train",
        json={"pairs": ["BTC/USDT", "ETH/USDT"], "candles": 1500, "timeframe": "4h"},
        headers={"X-Operator-Token": "secret-token"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["job_id"] == "job-9"
    assert payload["status"] == TRAINING_JOB_QUEUED
    assert payload["pairs"] == ["BTC/USDT", "ETH/USDT"]
    assert payload["candles"] == 1500
    assert payload["timeframe"] == "4h"

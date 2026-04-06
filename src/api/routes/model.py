"""Model health and training control endpoints."""

from __future__ import annotations

import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from api.dependencies import get_bot

router = APIRouter()


class TrainModelRequest(BaseModel):
    pairs: list[str] | None = None
    candles: int = Field(default=1000, gt=0)
    timeframe: str = "1h"


def _require_operator_token(
    x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
) -> None:
    expected_token = os.getenv("AKIBA_OPERATOR_TOKEN")
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="operator_token_not_configured",
        )
    if not x_operator_token or not secrets.compare_digest(
        x_operator_token, expected_token
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="operator_token_invalid",
        )


def _map_training_error(reason: str) -> HTTPException:
    if reason == "operator_token_invalid":
        return HTTPException(status_code=403, detail=reason)
    if reason == "operator_token_not_configured":
        return HTTPException(status_code=503, detail=reason)
    return HTTPException(status_code=409, detail=reason)


@router.get("/model/status")
async def get_model_status(bot=Depends(get_bot)):
    """Return FreqAI model health plus the current training snapshot."""
    model_snapshot = getattr(bot, "get_model_status_info", None)
    if callable(model_snapshot):
        return await model_snapshot()
    return {
        "freqai_active": False,
        "reason": "Model snapshot unavailable",
        "models": [],
    }


@router.get("/model/train/status")
async def get_model_training_status(bot=Depends(get_bot)):
    """Return the current and most recent training job state."""
    if not hasattr(bot, "get_training_job_status"):
        return {
            "current_job": None,
            "last_job": None,
            "can_start_training": False,
            "active_job_count": 0,
            "start_blocker": "training_control_unavailable",
        }

    snapshot = await bot.get_training_job_status()
    can_start_training, blocker = await bot.can_start_training()
    payload = snapshot.to_dict()
    payload["can_start_training"] = can_start_training
    payload["start_blocker"] = blocker
    return payload


@router.post("/model/train", dependencies=[Depends(_require_operator_token)])
async def start_model_training(
    request: TrainModelRequest,
    bot=Depends(get_bot),
):
    """Queue a server-side FreqAI training job."""
    can_start_training, blocker = await bot.can_start_training()
    if not can_start_training:
        raise _map_training_error(blocker or "training_start_blocked")
    try:
        queued_job = await bot.enqueue_training_job(
            requested_by="api",
            pairs=request.pairs,
            candles=request.candles,
            timeframe=request.timeframe,
        )
    except ValueError as exc:
        raise _map_training_error(str(exc)) from exc

    return queued_job.to_dict()

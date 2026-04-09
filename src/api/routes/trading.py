"""
Trading Control endpoints
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.dependencies import get_bot
from api.operator_auth import require_operator_token
from runtime.view_models import ProjectionSource

router = APIRouter()


class LiveRolloutApprovalRequest(BaseModel):
    target_profile: str
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    expires_in_hours: int | None = Field(default=24, gt=0)


class LiveRolloutRollbackRequest(BaseModel):
    actor: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    target_profile: str | None = None


@router.get("/trades")
async def get_active_trades(bot=Depends(get_bot)):
    """Get open positions from the active trading authority."""
    projection = bot.get_runtime_projection()
    if projection.source is ProjectionSource.RUNTIME_PROJECTION:
        trades = [
            {
                "position_id": position.position_id,
                "symbol": position.symbol,
                "side": position.side,
                "amount": position.open_qty,
                "entry_price": position.avg_entry_price,
                "stop_loss": position.stop_loss,
                "take_profit": position.take_profit,
                "trailing_stop_price": position.trailing_stop,
                "status": position.status,
                "source": projection.source.value,
            }
            for position in projection.positions
            if position.open_qty > 0
        ]
        return {
            "count": len(trades),
            "trades": trades,
            "source": projection.source.value,
            "runtime_mode": projection.runtime_mode,
        }

    trades = bot.get_active_trades()
    response = {
        "count": len(trades),
        "trades": trades,
        "source": "legacy",
        "runtime_mode": projection.runtime_mode,
    }
    if projection.source is ProjectionSource.SHADOW:
        response["shadow_projected_count"] = projection.active_position_count
    return response


@router.post("/trading/pause")
async def pause_trading(bot=Depends(get_bot)):
    """Pause the trading engine (stops scanning for new opportunities)."""
    bot.pause_trading()
    return {
        "status": "paused",
        "is_running": bot.is_running,
        "trading_paused": getattr(bot, "trading_paused", False),
    }


@router.post("/trading/resume")
async def resume_trading(bot=Depends(get_bot)):
    """Resume the trading engine."""
    bot.resume_trading()
    return {
        "status": "resumed",
        "is_running": bot.is_running,
        "trading_paused": getattr(bot, "trading_paused", False),
    }


@router.get("/trading/live/approval")
async def get_live_rollout_approval_status(bot=Depends(get_bot)):
    getter = getattr(bot, "get_live_rollout_approval_status", None)
    if not callable(getter):
        return {
            "target_profile": None,
            "approval_required": False,
            "current_approval": None,
            "latest_approval": None,
            "latest_event": None,
        }
    snapshot = await getter()
    return snapshot.to_dict()


@router.post(
    "/trading/live/approve",
    dependencies=[Depends(require_operator_token)],
)
async def approve_live_rollout(
    request: LiveRolloutApprovalRequest,
    bot=Depends(get_bot),
):
    helper = getattr(bot, "approve_live_rollout", None)
    if not callable(helper):
        raise HTTPException(status_code=503, detail="live_rollout_approval_unavailable")
    try:
        approval = await helper(
            actor=request.actor,
            reason=request.reason,
            target_profile=request.target_profile,
            expires_in_hours=request.expires_in_hours,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    snapshot = await bot.get_live_rollout_approval_status()
    return {
        "approval": approval.to_dict(),
        "status": snapshot.to_dict(),
    }


@router.post(
    "/trading/live/rollback",
    dependencies=[Depends(require_operator_token)],
)
async def record_live_rollout_rollback(
    request: LiveRolloutRollbackRequest,
    bot=Depends(get_bot),
):
    helper = getattr(bot, "record_live_rollback", None)
    if not callable(helper):
        raise HTTPException(status_code=503, detail="live_rollout_approval_unavailable")
    try:
        approvals = await helper(
            actor=request.actor,
            reason=request.reason,
            target_profile=request.target_profile,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    snapshot = await bot.get_live_rollout_approval_status()
    return {
        "revoked_approvals": [approval.to_dict() for approval in approvals],
        "status": snapshot.to_dict(),
    }

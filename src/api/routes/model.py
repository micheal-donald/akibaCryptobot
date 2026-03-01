"""
Model status endpoint — exposes FreqAI model health to the dashboard.
"""

import os
from datetime import datetime
from fastapi import APIRouter, Depends
from api.dependencies import get_bot

router = APIRouter()


@router.get("/model/status")
async def get_model_status(bot=Depends(get_bot)):
    """
    Get information about the currently loaded FreqAI models:
    model age, accuracy, feature count, and training date.
    """
    active_strategy = bot.strategy_manager.get_active_strategy()

    # Check if this is an ensemble strategy with ML components
    if not hasattr(active_strategy, "strategies"):
        return {"freqai_active": False, "reason": "Active strategy is not an ensemble"}

    models = []
    for strategy, weight, name in active_strategy.strategies:
        if not hasattr(strategy, "model_trained"):
            continue

        model_info = {
            "name": name,
            "type": type(strategy).__name__,
            "weight": weight,
            "trained": getattr(strategy, "model_trained", False),
            "feature_count": len(getattr(strategy, "feature_columns", [])),
        }

        # Check model file age on disk
        models_dir = "models"
        if os.path.exists(models_dir):
            model_files = [
                f for f in os.listdir(models_dir) if f.endswith("_freqai.joblib")
            ]
            if model_files:
                newest = max(
                    model_files,
                    key=lambda f: os.path.getmtime(os.path.join(models_dir, f)),
                )
                mtime = os.path.getmtime(os.path.join(models_dir, newest))
                model_info["last_trained"] = datetime.fromtimestamp(mtime).isoformat()
                age_hours = (datetime.now().timestamp() - mtime) / 3600
                model_info["age_hours"] = round(age_hours, 1)
                model_info["stale"] = age_hours > 48  # Flag if older than 2 days

        # Top features if available
        if hasattr(strategy, "ml_model") and strategy.ml_model is not None:
            if hasattr(strategy.ml_model, "feature_importances_"):
                cols = getattr(strategy, "feature_columns", [])
                importances = strategy.ml_model.feature_importances_
                if len(cols) == len(importances):
                    top = sorted(
                        zip(cols, importances), key=lambda x: x[1], reverse=True
                    )[:5]
                    model_info["top_features"] = [
                        {"name": n, "importance": round(float(v), 4)} for n, v in top
                    ]

        models.append(model_info)

    any_trained = any(m["trained"] for m in models)
    return {
        "freqai_active": any_trained,
        "models": models,
    }

"""Shared operator token validation for mutating control-plane endpoints."""

from __future__ import annotations

import os
import secrets

from fastapi import Header, HTTPException, status


def require_operator_token(
    x_operator_token: str | None = Header(default=None, alias="X-Operator-Token"),
) -> None:
    expected_token = os.getenv("AKIBA_OPERATOR_TOKEN")
    if not expected_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="operator_token_not_configured",
        )
    if not x_operator_token or not secrets.compare_digest(
        x_operator_token,
        expected_token,
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="operator_token_invalid",
        )

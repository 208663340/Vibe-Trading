"""eToro copy-trading workflows."""

from __future__ import annotations

import math
import re
import time
import uuid
from typing import Any

from src.markets.trading.connectors.etoro.client import (
    COPY_TRADING_PAPER_UNSUPPORTED,
    EtoroAPIError,
    EtoroConfig,
    copy_root,
    make_client,
)

# ``COPY_TRADING_PAPER_UNSUPPORTED`` is defined beside ``copy_root``, which is
# what structurally enforces it; service.py still imports it from here.
_REFERENCE_ID_RE = re.compile(r"^[A-Za-z0-9._~-]{1,35}$")


def _base(cfg: EtoroConfig) -> dict[str, Any]:
    return {
        "profile": cfg.profile,
        "environment": cfg.environment,
        "paper_guard": "path_separated_key_bound",
    }


def _error(cfg: EtoroConfig, message: str, **extra: Any) -> dict[str, Any]:
    return {"status": "error", "error": message, **_base(cfg), **extra}


def _reject_copy_on_paper(cfg: EtoroConfig) -> dict[str, Any] | None:
    if cfg.is_paper:
        return _error(
            cfg,
            COPY_TRADING_PAPER_UNSUPPORTED,
            error_code="copy_unavailable_on_paper",
        )
    return None


def copy_precheck(
    config: EtoroConfig | None = None,
    *,
    parent_cid: int,
    amount: float,
    request_id: str | None = None,
) -> dict[str, Any]:
    from src.markets.trading.connectors.etoro.client import load_config

    cfg = config or load_config()
    blocked = _reject_copy_on_paper(cfg)
    if blocked is not None:
        return blocked
    try:
        clean_amount = float(amount)
    except (TypeError, ValueError, OverflowError):
        return _error(cfg, "amount must be a finite positive number in account currency")
    if not math.isfinite(clean_amount) or clean_amount <= 0:
        return _error(cfg, "amount must be a finite positive number in account currency")

    body = {"parentCID": int(parent_cid), "amount": clean_amount}
    req_id = (request_id or str(uuid.uuid4())).strip()
    path = f"{copy_root(cfg)}/eligibility"
    try:
        payload = make_client(cfg).request("POST", path, json_body=body, request_id=req_id)
    except EtoroAPIError as exc:
        return _error(cfg, str(exc), request_id=req_id)

    return {"status": "ok", **_base(cfg), "request_id": req_id, "result": payload}


def copy_start_or_adjust(
    config: EtoroConfig | None = None,
    *,
    parent_cid: int,
    amount: float,
    reference_id: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    from src.markets.trading.connectors.etoro.client import load_config

    cfg = config or load_config()
    blocked = _reject_copy_on_paper(cfg)
    if blocked is not None:
        return blocked
    try:
        clean_amount = float(amount)
    except (TypeError, ValueError, OverflowError):
        return _error(cfg, "amount must be a finite non-zero number in account currency")
    if not math.isfinite(clean_amount) or clean_amount == 0:
        return _error(cfg, "amount must be a finite non-zero number in account currency")
    ref = str(reference_id or "").strip()
    if not _REFERENCE_ID_RE.fullmatch(ref):
        return _error(
            cfg,
            "reference_id must be 1-35 URL-safe characters (letters, digits, '.', '_', '~', '-')",
        )

    body = {
        "parentCID": int(parent_cid),
        "amount": clean_amount,
        "referenceID": ref,
    }
    req_id = (request_id or str(uuid.uuid4())).strip()
    path = copy_root(cfg)
    try:
        payload = make_client(cfg).request("POST", path, json_body=body, request_id=req_id)
    except EtoroAPIError as exc:
        return _error(cfg, str(exc), request_id=req_id)

    return {
        "status": "ok",
        **_base(cfg),
        "parent_cid": int(parent_cid),
        "amount": clean_amount,
        "request_id": req_id,
        "reference_id": ref,
        "token": _extract_token(payload),
        "raw": payload,
    }


def copy_poll(
    config: EtoroConfig | None = None,
    *,
    reference_id: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    from src.markets.trading.connectors.etoro.client import load_config

    cfg = config or load_config()
    blocked = _reject_copy_on_paper(cfg)
    if blocked is not None:
        return blocked
    ref = str(reference_id or "").strip()
    if not ref:
        return _error(cfg, "reference_id is required")

    req_id = (request_id or str(uuid.uuid4())).strip()
    path = f"{copy_root(cfg)}/{ref}"
    try:
        payload = make_client(cfg).request("GET", path, request_id=req_id, allow_retry=True)
    except EtoroAPIError as exc:
        return _error(cfg, str(exc), reference_id=ref, request_id=req_id)

    return {"status": "ok", **_base(cfg), "reference_id": ref, "request_id": req_id, "result": payload}


def copy_close(
    config: EtoroConfig | None = None,
    *,
    mirror_id: int,
    unregister_type: str = "Close",
    request_id: str | None = None,
) -> dict[str, Any]:
    from src.markets.trading.connectors.etoro.client import load_config

    cfg = config or load_config()
    blocked = _reject_copy_on_paper(cfg)
    if blocked is not None:
        return blocked
    action = str(unregister_type or "Close").strip()
    if action not in ("Close", "Detach"):
        return _error(cfg, "unregister_type must be 'Close' or 'Detach'")

    req_id = (request_id or str(uuid.uuid4())).strip()
    path = f"{copy_root(cfg)}/close"
    body = {"mirrorID": int(mirror_id), "unregisterType": action}
    try:
        payload = make_client(cfg).request("POST", path, json_body=body, request_id=req_id)
    except EtoroAPIError as exc:
        return _error(cfg, str(exc), mirror_id=mirror_id, request_id=req_id)

    return {
        "status": "ok",
        **_base(cfg),
        "mirror_id": int(mirror_id),
        "unregister_type": action,
        "request_id": req_id,
        "token": _extract_token(payload),
        "raw": payload,
    }


def copy_poll_until_complete(
    config: EtoroConfig | None = None,
    *,
    reference_id: str,
    timeout_s: float = 30.0,
    interval_s: float = 1.0,
) -> dict[str, Any]:
    from src.markets.trading.connectors.etoro.client import load_config

    cfg = config or load_config()
    blocked = _reject_copy_on_paper(cfg)
    if blocked is not None:
        return blocked
    deadline = time.monotonic() + max(1.0, float(timeout_s))
    last: dict[str, Any] = {}
    while time.monotonic() < deadline:
        last = copy_poll(cfg, reference_id=reference_id)
        if last.get("status") != "ok":
            return last
        result = last.get("result")
        if isinstance(result, dict) and _is_copy_complete(result):
            last["completed"] = True
            return last
        time.sleep(max(0.2, float(interval_s)))
    last["completed"] = False
    last["status"] = "error"
    last["error"] = "copy operation polling timed out"
    return last


def _extract_token(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    for key in ("token", "Token"):
        value = payload.get(key)
        if value is not None:
            return str(value)
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("token", "Token"):
            value = data.get(key)
            if value is not None:
                return str(value)
    return None


def _is_copy_complete(payload: dict[str, Any]) -> bool:
    for key in ("isCompleted", "isSuccess", "completed"):
        value = payload.get(key)
        if isinstance(value, bool):
            return value
    status = payload.get("status")
    if isinstance(status, str):
        return status.lower() in ("completed", "success", "done")
    return False

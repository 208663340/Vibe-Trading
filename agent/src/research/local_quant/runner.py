"""Subprocess entry point for a captured local quant job."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from src.platform.core.state import RunStateStore
from src.research.local_quant.pipeline import execute_job


def main():
    run_dir = Path(sys.argv[1]).resolve()
    try:
        summary = execute_job(run_dir)
    except Exception as exc:
        RunStateStore().mark_failure(run_dir, str(exc))
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1) from exc
    RunStateStore().mark_success(run_dir)
    print(json.dumps({"status": "ok", "metrics": summary["metrics"]}, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()

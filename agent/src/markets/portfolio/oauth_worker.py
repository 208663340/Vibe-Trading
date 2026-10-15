"""Isolated worker for an interactive portfolio OAuth reconnect.

OAuth libraries may leave their loopback callback server alive after a failed
handshake. Running the flow in a short-lived process guarantees that the
callback port is released on success, failure, cancellation, or timeout.
"""

from __future__ import annotations

import argparse

from src.markets.portfolio.service import PortfolioService


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_id")
    args = parser.parse_args()
    try:
        PortfolioService().reconnect_source(args.source_id)
    except BaseException:  # The parent reports a redacted, actionable error.
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

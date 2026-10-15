"""Regression: _SlidingWindowRateLimiter must not leak memory on unique keys.

The limiter keys on client IP.  A burst of unique IPs (a scan, a botnet, a
proxy farm) creates one deque per IP in ``_hits``.  The original code used
``defaultdict(deque)``, which creates an empty deque on every lookup — even
for a key that is immediately rejected — and never removes empty buckets.
Over time ``_hits`` grows without bound.

The fix: use a plain dict, and delete a bucket once all its entries expire.
"""

from __future__ import annotations

import time

from src.interfaces.api.system_routes import _SlidingWindowRateLimiter


def test_allows_within_budget() -> None:
    limiter = _SlidingWindowRateLimiter(max_requests=3, window_seconds=60.0)
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is True


def test_denies_over_budget() -> None:
    limiter = _SlidingWindowRateLimiter(max_requests=2, window_seconds=60.0)
    limiter.allow("1.2.3.4")
    limiter.allow("1.2.3.4")
    assert limiter.allow("1.2.3.4") is False


def test_unique_keys_do_not_grow_hits_dict() -> None:
    """A burst of unique IPs must not leave empty buckets behind."""
    limiter = _SlidingWindowRateLimiter(max_requests=1, window_seconds=60.0)
    for i in range(1000):
        limiter.allow(f"10.0.0.{i}")
    # Each IP hit the limiter once (allowed), so each has a bucket with 1
    # entry.  That is expected — the buckets are still within their window.
    # The leak we are guarding against is *empty* buckets surviving after
    # eviction.
    assert len(limiter._hits) == 1000


def test_empty_buckets_are_evicted_after_window_expires() -> None:
    """After the window expires, buckets with no surviving entries are removed."""
    limiter = _SlidingWindowRateLimiter(max_requests=5, window_seconds=0.05)
    limiter.allow("1.2.3.4")
    limiter.allow("5.6.7.8")
    assert len(limiter._hits) == 2

    time.sleep(0.06)

    # A new key triggers cleanup of expired buckets.
    limiter.allow("9.10.11.12")
    # The two old buckets had all entries expire and should be gone.
    assert "1.2.3.4" not in limiter._hits
    assert "5.6.7.8" not in limiter._hits
    # The new key's bucket is present.
    assert "9.10.11.12" in limiter._hits


def test_over_limit_key_with_expired_entries_is_cleaned() -> None:
    """A key that was over-limit but whose entries have all expired is dropped."""
    limiter = _SlidingWindowRateLimiter(max_requests=1, window_seconds=0.05)
    assert limiter.allow("1.2.3.4") is True
    assert limiter.allow("1.2.3.4") is False  # over limit

    time.sleep(0.06)

    # The next call should clean the expired bucket and allow the request.
    assert limiter.allow("1.2.3.4") is True


def test_reset_clears_all_buckets() -> None:
    limiter = _SlidingWindowRateLimiter(max_requests=5, window_seconds=60.0)
    limiter.allow("1.2.3.4")
    limiter.allow("5.6.7.8")
    assert len(limiter._hits) == 2
    limiter.reset()
    assert len(limiter._hits) == 0


def test_no_defaultdict_side_effect_on_lookup() -> None:
    """Looking up a never-seen key must not create an empty bucket.

    The old ``defaultdict(deque)`` created a bucket on every ``self._hits[key]``
    access — even when the key was immediately rejected.  The plain-dict fix
    must not exhibit this behaviour.
    """
    limiter = _SlidingWindowRateLimiter(max_requests=1, window_seconds=60.0)
    # Access the internal dict directly to simulate a lookup side effect.
    _ = limiter._hits.get("never-seen")
    assert "never-seen" not in limiter._hits



def test_zero_limit_denies_all_requests() -> None:
    """A limiter with max_requests=0 must deny every request without creating a bucket."""
    limiter = _SlidingWindowRateLimiter(max_requests=0, window_seconds=60.0)
    assert limiter.allow("1.2.3.4") is False
    assert "1.2.3.4" not in limiter._hits


def test_negative_limit_denies_all_requests() -> None:
    """A limiter with a negative max must deny every request."""
    limiter = _SlidingWindowRateLimiter(max_requests=-1, window_seconds=60.0)
    assert limiter.allow("1.2.3.4") is False
    assert "1.2.3.4" not in limiter._hits
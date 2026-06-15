"""In-process refresh-spam guard for the dashboard shell (GET /).

A human mashing reload — or a bot hammering the SPA shell — is first slowed with
a short buffer, then, if it keeps up, locked out and bounced to the login screen.

State is per-client-IP and held in memory. This is an abuse guard for a single-
process app, so losing the counters on restart is fine (and simply clears any
active lockout). It deliberately does not touch the auth DB or session crypto.

Policy (PageGuard defaults — all overridable, e.g. for tests):
  • Up to `burst` loads within `window` seconds   → served immediately ("ok").
  • The next loads within the window              → `throttle_seconds` delay
                                                     ("throttle" — the buffer).
  • If loads keep coming past `lockout_at` in the → `lockout_seconds` hard
    window                                          lockout ("lockout": the
                                                     caller is logged out and
                                                     sent back to /login).
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable

OK = "ok"
THROTTLE = "throttle"
LOCKOUT = "lockout"


@dataclass
class PageGuard:
    window: float = 5.0            # sliding window, seconds
    burst: int = 5                 # loads allowed at full speed within the window
    throttle_seconds: float = 3.0  # the "buffer" added once burst is exceeded
    lockout_at: int = 10           # loads within the window that trip the lockout
    lockout_seconds: float = 120.0
    clock: Callable[[], float] = time.monotonic
    _hits: dict[str, deque] = field(default_factory=lambda: defaultdict(deque))
    _locked_until: dict[str, float] = field(default_factory=dict)

    def locked_remaining(self, key: str) -> float:
        """Seconds left on an active lockout for `key` (0.0 if not locked)."""
        until = self._locked_until.get(key)
        if until is None:
            return 0.0
        remaining = until - self.clock()
        if remaining <= 0:
            self._locked_until.pop(key, None)
            return 0.0
        return remaining

    def check(self, key: str) -> tuple[str, float]:
        """Record a page load for `key` and decide what to do with it.

        Returns ``(action, seconds)`` where action is OK / THROTTLE / LOCKOUT and
        seconds is the throttle delay (THROTTLE) or the remaining lockout time
        (LOCKOUT). Callers should not record the load any other way — calling this
        *is* the record.
        """
        remaining = self.locked_remaining(key)
        if remaining > 0:                       # already serving a lockout
            return LOCKOUT, remaining

        now = self.clock()
        hits = self._hits[key]
        hits.append(now)
        cutoff = now - self.window
        while hits and hits[0] < cutoff:        # drop loads outside the window
            hits.popleft()
        count = len(hits)

        if count > self.lockout_at:             # spamming kept up → hard lockout
            self._locked_until[key] = now + self.lockout_seconds
            hits.clear()
            return LOCKOUT, self.lockout_seconds
        if count > self.burst:                  # over the burst → add the buffer
            return THROTTLE, self.throttle_seconds
        return OK, 0.0

    def reset(self, key: str | None = None) -> None:
        """Clear counters/lockout for one key, or everything when key is None."""
        if key is None:
            self._hits.clear()
            self._locked_until.clear()
        else:
            self._hits.pop(key, None)
            self._locked_until.pop(key, None)


# Module-level singleton used by the app (tests may swap or reset it).
page_guard = PageGuard()

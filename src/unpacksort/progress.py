"""Typed, rate-limited progress for interactive runs and rotating logs."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Literal, Protocol, Self

ProgressPhase = Literal[
    "fingerprint",
    "discovery",
    "expand",
    "plan",
    "publish",
    "report",
    "completion",
]
ETA_MIN_SAMPLES = 5


@dataclass(frozen=True)
class ProgressEvent:
    """One stable operational snapshot shared by renderers and tests."""

    phase: ProgressPhase
    current: int
    total: int | None
    durable: int
    failures: int
    rate: float
    elapsed: float
    eta_seconds: float | None


class ProgressSink(Protocol):
    """Minimal progress interface accepted by the processing engine."""

    def phase(self, phase: ProgressPhase, *, total: int | None = None) -> None:
        """Begin a named phase."""
        ...

    def advance(self, count: int = 1) -> None:
        """Record completed work."""
        ...

    def committed(self, count: int = 1) -> None:
        """Record durable publication."""
        ...

    def failed(self, count: int = 1) -> None:
        """Record failed work."""
        ...


class NullProgress:
    """No-op sink used by the library API unless a renderer is supplied."""

    def phase(self, phase: ProgressPhase, *, total: int | None = None) -> None:
        """Ignore a phase transition."""
        del phase, total

    def advance(self, count: int = 1) -> None:
        """Ignore completed work."""
        del count

    def committed(self, count: int = 1) -> None:
        """Ignore durable publication."""
        del count

    def failed(self, count: int = 1) -> None:
        """Ignore failed work."""
        del count


class Progress:
    """Keep long silent phases visibly alive without unbounded event retention."""

    def __init__(self, *, heartbeat_seconds: float = 5.0) -> None:
        """Start a daemon heartbeat at the requested bounded interval."""
        self._logger = logging.getLogger("unpacksort.progress")
        self._heartbeat_seconds = heartbeat_seconds
        self._started = time.monotonic()
        self._phase_started = self._started
        self._phase: ProgressPhase = "fingerprint"
        self._current = 0
        self._total: int | None = None
        self._durable = 0
        self._failures = 0
        self._last_emit = 0.0
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._heartbeat,
            name="unpacksort-progress",
            daemon=True,
        )
        self._thread.start()

    def phase(self, phase: ProgressPhase, *, total: int | None = None) -> None:
        """Begin a phase and emit its initial snapshot."""
        with self._lock:
            self._phase = phase
            self._current = 0
            self._total = total
            self._phase_started = time.monotonic()
            self._emit(force=True)

    def advance(self, count: int = 1) -> None:
        """Advance the active phase."""
        with self._lock:
            self._current += count
            self._emit(force=False)

    def committed(self, count: int = 1) -> None:
        """Increase the durable publication count."""
        with self._lock:
            self._durable += count
            self._emit(force=False)

    def failed(self, count: int = 1) -> None:
        """Increase the failure count and flush a snapshot."""
        with self._lock:
            self._failures += count
            self._emit(force=True)

    def event(self) -> ProgressEvent:
        """Return the current typed snapshot."""
        phase_elapsed = max(time.monotonic() - self._phase_started, 1e-6)
        rate = self._current / phase_elapsed
        eta = None
        if self._total is not None and self._current >= ETA_MIN_SAMPLES and rate > 0:
            eta = max(0.0, self._total - self._current) / rate
        return ProgressEvent(
            phase=self._phase,
            current=self._current,
            total=self._total,
            durable=self._durable,
            failures=self._failures,
            rate=rate,
            elapsed=max(0.0, time.monotonic() - self._started),
            eta_seconds=eta,
        )

    def _emit(self, *, force: bool) -> None:
        now = time.monotonic()
        if not force and now - self._last_emit < 1.0:
            return
        event = self.event()
        total = "?" if event.total is None else f"{event.total:,}"
        eta = "" if event.eta_seconds is None else f" eta={event.eta_seconds:.0f}s"
        self._logger.info(
            "progress phase=%s current=%s total=%s durable=%s failures=%s "
            "rate=%.1f/s elapsed=%.1fs%s",
            event.phase,
            f"{event.current:,}",
            total,
            f"{event.durable:,}",
            f"{event.failures:,}",
            event.rate,
            event.elapsed,
            eta,
        )
        self._last_emit = now

    def _heartbeat(self) -> None:
        while not self._stop.wait(self._heartbeat_seconds):
            with self._lock:
                self._emit(force=True)

    def close(self) -> None:
        """Stop heartbeat, emit completion, and flush every handler."""
        self._stop.set()
        self._thread.join(timeout=1.0)
        with self._lock:
            self._phase = "completion"
            self._current = self._durable
            self._total = self._durable
            self._emit(force=True)
        for handler in logging.getLogger().handlers:
            handler.flush()

    def __enter__(self) -> Self:
        """Return the active renderer."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Flush and stop the renderer."""
        self.close()

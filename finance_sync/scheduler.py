"""Background synchronization scheduler.

Runs the SyncEngine in a daemon thread every ``SYNC_INTERVAL_HOURS`` (default
12) without ever blocking the UI. Also powers manual "refresh now" requests
from the API: those run on short-lived worker threads and the UI polls
:meth:`SyncScheduler.status`.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta
from typing import Optional

from .engine import SyncEngine

logger = logging.getLogger("finance_sync.scheduler")


class SyncScheduler:
    """Owns the periodic sync loop and on-demand background syncs."""

    def __init__(self, app, interval_hours: float = 12.0):
        self.app = app
        self.interval = timedelta(hours=interval_hours)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._busy = threading.Lock()   # one sync at a time
        self._state_lock = threading.Lock()
        self._state = {
            "running": False,
            "last_started": None,
            "last_finished": None,
            "last_trigger": None,
            "last_status": None,
            "next_scheduled": None,
        }

    # -- lifecycle ------------------------------------------------------------

    def start(self) -> None:
        """Start the periodic loop (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="finance-sync-scheduler", daemon=True)
        self._thread.start()
        logger.info("Background sync scheduler started (every %s)", self.interval)

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        # First pass shortly after startup so a stale local DB catches up,
        # then steady-state every `interval`.
        if self._stop.wait(timeout=10):
            return
        while not self._stop.is_set():
            if self._due_for_scheduled_sync():
                self.run_sync(trigger="scheduled", wait=True)
            with self._state_lock:
                self._state["next_scheduled"] = (
                    datetime.utcnow() + self.interval).isoformat()
            if self._stop.wait(timeout=self.interval.total_seconds()):
                return

    def _due_for_scheduled_sync(self) -> bool:
        """Skip the startup pass when a sync ran within the interval."""
        from models import SyncRun
        with self.app.app_context():
            last = (SyncRun.query.filter(SyncRun.status != "running")
                    .order_by(SyncRun.started_at.desc()).first())
            if last is None:
                return True
            return datetime.utcnow() - last.started_at >= self.interval

    # -- on-demand syncs ---------------------------------------------------------

    def run_sync(self, trigger: str = "manual",
                 connection_id: Optional[int] = None,
                 wait: bool = False,
                 queue: bool = False) -> bool:
        """Run a sync (all connections, or one).

        Only one sync runs at a time. If one is already in progress the call
        returns ``False`` — unless ``queue=True``, in which case the sync
        waits its turn on a background thread (used for the initial sync of a
        freshly connected institution, so rapid connects never drop a sync).

        With ``wait=False`` (API default) the sync runs on a background thread
        and the caller polls :meth:`status` — the UI is never blocked.
        """
        wait = wait or bool(self.app.config.get("SYNC_SYNCHRONOUS"))

        def _work(pre_acquired: bool) -> None:
            if not pre_acquired:
                self._busy.acquire()  # queued: wait for the in-flight sync
            with self._state_lock:
                self._state["running"] = True
                self._state["last_started"] = datetime.utcnow().isoformat()
                self._state["last_trigger"] = trigger
            try:
                with self.app.app_context():
                    engine = SyncEngine()
                    if connection_id is not None:
                        result = engine.sync_connection(connection_id, trigger=trigger)
                        status = result.status
                    else:
                        result = engine.sync_all(trigger=trigger)
                        status = result.status
                    with self._state_lock:
                        self._state["last_status"] = status
            except Exception:
                logger.exception("Background sync crashed")
                with self._state_lock:
                    self._state["last_status"] = "error"
            finally:
                with self._state_lock:
                    self._state["running"] = False
                    self._state["last_finished"] = datetime.utcnow().isoformat()
                self._busy.release()

        pre_acquired = self._busy.acquire(blocking=False)
        if not pre_acquired and not queue:
            return False
        if wait:
            _work(pre_acquired)
        else:
            threading.Thread(target=_work, args=(pre_acquired,),
                             name=f"finance-sync-{trigger}", daemon=True).start()
        return True

    def status(self) -> dict:
        with self._state_lock:
            return dict(self._state)


# Module-level singleton, installed by app.create_app().
_scheduler: Optional[SyncScheduler] = None


def init_scheduler(app, interval_hours: float = 12.0,
                   autostart: bool = True) -> SyncScheduler:
    """Create (or return) the process-wide scheduler for this app."""
    global _scheduler
    if _scheduler is None:
        _scheduler = SyncScheduler(app, interval_hours=interval_hours)
        if autostart:
            _scheduler.start()
    return _scheduler


def get_scheduler() -> Optional[SyncScheduler]:
    return _scheduler

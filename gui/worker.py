"""Background worker thread that keeps the GUI responsive during long operations.

The wrapper supports cooperative cancellation: every time the wrapped function
emits a log message through ``log_fn``, the worker checks a shared
``threading.Event`` flag and raises ``WorkerCancelled`` if the GUI has asked
the worker to stop.  This means any long-running step that periodically logs
progress (the Sentinel-2 / NLCD downloaders, the per-AOI orchestrators, …)
becomes cancellable without changes to the step code itself.

Hard cancellation (``QThread.terminate``) is only used as a last resort by the
main window's ``closeEvent`` when the wrapped function is stuck inside a
blocking C call (e.g. a single ``requests.get`` with a 5-minute timeout).
"""
import threading
import traceback

from PyQt6.QtCore import QThread, pyqtSignal


class WorkerCancelled(Exception):
    """Raised inside the wrapped function via ``log_fn`` when the GUI has
    asked the worker to cancel.  Caught by ``Worker.run`` so the cancel
    path produces a clean log line instead of a traceback."""


class Worker(QThread):
    """Generic QThread wrapper.

    Usage::

        worker = Worker(my_function, kwarg1=val1, kwarg2=val2)
        worker.message.connect(log_panel.append)
        worker.finished.connect(on_done)   # receives result dict or {}
        worker.error.connect(on_error)     # receives error string
        worker.start()

    The wrapped function receives an extra ``log_fn`` keyword argument
    that emits messages to the GUI log panel.  Each ``log_fn`` call also
    acts as a cancellation checkpoint — if ``worker.cancel()`` has been
    called from the GUI thread, the next ``log_fn`` raises
    ``WorkerCancelled`` to unwind the wrapped function.
    """

    message = pyqtSignal(str)
    finished = pyqtSignal(object)   # result (dict or any)
    error = pyqtSignal(str)
    cancelled = pyqtSignal()

    def __init__(self, fn, **kwargs):
        super().__init__()
        self._fn = fn
        self._kwargs = kwargs
        self._cancel_event = threading.Event()

    # ── Cancellation API (called from GUI thread) ────────────────────────
    def cancel(self):
        """Ask the worker to stop at its next ``log_fn`` checkpoint."""
        self._cancel_event.set()

    def is_cancelled(self) -> bool:
        return self._cancel_event.is_set()

    # ── Thread body ─────────────────────────────────────────────────────
    def _emit_log(self, msg):
        # Check the cancel flag before emitting — this is the cooperative
        # checkpoint the wrapped function uses.  Raised inside the worker
        # thread, the exception unwinds through the wrapped function and
        # is caught below.
        if self._cancel_event.is_set():
            raise WorkerCancelled("Worker cancelled by user.")
        self.message.emit(str(msg))

    def run(self):
        try:
            kw = dict(self._kwargs)
            kw["log_fn"] = self._emit_log
            result = self._fn(**kw)
            self.finished.emit(result if result is not None else {})
        except WorkerCancelled:
            self.message.emit("⚠️  Operation cancelled.")
            self.cancelled.emit()
        except Exception as exc:
            self.error.emit(f"{exc}\n\n{traceback.format_exc()}")

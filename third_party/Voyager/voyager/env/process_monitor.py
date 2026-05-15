import time
import re
import warnings
from typing import List

import subprocess
import logging
import threading
import os

import voyager.utils as U
from voyager.utils.console import safe_print as print

try:
    import psutil
except ImportError:  # pragma: no cover - compatibility fallback
    class _CompatPopen(subprocess.Popen):
        def is_running(self):
            return self.poll() is None

    class _PsutilCompat:
        Popen = _CompatPopen

    psutil = _PsutilCompat()


class SubprocessMonitor:
    def __init__(
        self,
        commands: List[str],
        name: str,
        ready_match: str = r".*",
        log_path: str = "logs",
        callback_match: str = r"^(?!x)x$",  # regex that will never match
        callback: callable = None,
        finished_callback: callable = None,
        stop_timeout_seconds: float = 10.0,
        max_restart_backoff_seconds: float = 8.0,
    ):
        self.commands = commands
        start_time = time.strftime("%Y%m%d_%H%M%S")
        self.name = name
        self.logger = logging.getLogger(name)
        handler = logging.FileHandler(U.f_join(log_path, f"{start_time}.log"))
        formatter = logging.Formatter(
            "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        handler.setFormatter(formatter)
        self.logger.addHandler(handler)
        self.logger.setLevel(logging.INFO)
        self.process = None
        self.ready_match = ready_match
        self.ready_event = None
        self.ready_line = None
        self.callback_match = callback_match
        self.callback = callback
        self.finished_callback = finished_callback
        self.thread = None
        self.stop_timeout_seconds = stop_timeout_seconds
        self.max_restart_backoff_seconds = max_restart_backoff_seconds
        self._consecutive_failures = 0
        self._next_start_after = 0.0

    def _start(self):
        self.logger.info(f"Starting subprocess with commands: {self.commands}")

        self.process = psutil.Popen(
            self.commands,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env={
                **os.environ,
                "PYTHONIOENCODING": os.environ.get("PYTHONIOENCODING", "utf-8"),
                "PYTHONUTF8": os.environ.get("PYTHONUTF8", "1"),
            },
        )
        print(f"Subprocess {self.name} started with PID {self.process.pid}.")
        try:
            for line in iter(self.process.stdout.readline, ""):
                self.logger.info(line.strip())
                if re.search(self.ready_match, line):
                    self.ready_line = line
                    self.logger.info("Subprocess is ready.")
                    self.ready_event.set()
                    self._consecutive_failures = 0
                    self._next_start_after = 0.0
                if re.search(self.callback_match, line) and self.callback:
                    self.callback()
        finally:
            if not self.ready_event.is_set():
                self.ready_event.set()
                self._consecutive_failures += 1
                backoff_seconds = min(
                    2 ** max(self._consecutive_failures - 1, 0),
                    self.max_restart_backoff_seconds,
                )
                self._next_start_after = time.time() + backoff_seconds
                warnings.warn(f"Subprocess {self.name} failed to start.")
            if self.finished_callback:
                self.finished_callback()

    def run(self):
        if self.process and self.process.is_running():
            return
        wait_seconds = self._next_start_after - time.time()
        if wait_seconds > 0:
            time.sleep(wait_seconds)
        self.ready_event = threading.Event()
        self.ready_line = None
        self.thread = threading.Thread(target=self._start)
        self.thread.start()
        self.ready_event.wait()

    def stop(self):
        self.logger.info("Stopping subprocess.")
        if self.process and self.process.is_running():
            self.process.terminate()
            try:
                self.process.wait(timeout=self.stop_timeout_seconds)
            except Exception:
                self.process.kill()
                self.process.wait(timeout=self.stop_timeout_seconds)
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=self.stop_timeout_seconds)

    # def __del__(self):
    #     if self.process.is_running():
    #         self.stop()

    @property
    def is_running(self):
        if self.process is None:
            return False
        return self.process.is_running()

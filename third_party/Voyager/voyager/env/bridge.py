import concurrent.futures
import os.path
import socket
import time
import warnings
from datetime import datetime
from typing import SupportsFloat, Any, Tuple, Dict

import requests
import json

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency
    psutil = None

import gymnasium as gym
from gymnasium.core import ObsType

import voyager.utils as U
from voyager.utils.console import safe_print as print

from .minecraft_launcher import MinecraftInstance
from .process_monitor import SubprocessMonitor


class VoyagerEnv(gym.Env):
    def __init__(
        self,
        mc_port=None,
        azure_login=None,
        server_host="http://127.0.0.1",
        server_port=3000,
        request_timeout=600,
        log_path="./logs",
    ):
        if not mc_port and not azure_login:
            raise ValueError("Either mc_port or azure_login must be specified")
        if mc_port and azure_login:
            warnings.warn(
                "Both mc_port and mc_login are specified, mc_port will be ignored"
            )
        self.mc_port = mc_port
        self.azure_login = azure_login
        self.server = f"{server_host}:{server_port}"
        self.server_port = server_port
        self.request_timeout = request_timeout
        self.log_path = log_path
        self.mineflayer = self.get_mineflayer_process(server_port)
        if azure_login:
            self.mc_instance = self.get_mc_instance()
        else:
            self.mc_instance = None
        self.has_reset = False
        self.reset_options = None
        self.connected = False
        self.server_paused = False

    def _is_minecraft_unavailable_error(self, err: Exception | str | None) -> bool:
        message = str(err or "")
        lowered = message.lower()
        if not lowered:
            return False
        if "econnrefused" in lowered or "connection refused" in lowered:
            return True
        if "connection reset" in lowered or "econnreset" in lowered:
            return True
        if "read timed out" in lowered or "read timeout" in lowered:
            return True
        if "timeout waiting for " in lowered and " ticks after " in lowered:
            return True
        if "minecraft server reply with code 400" in lowered and '"port":25565' in lowered:
            return True
        if "bot not spawned" in lowered:
            return True
        return False

    def _wait_for_server_port_state(self, should_be_open, timeout_seconds=10):
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self._server_port_open() == should_be_open:
                return True
            time.sleep(0.25)
        return self._server_port_open() == should_be_open

    def get_mineflayer_process(self, server_port):
        U.f_mkdir(self.log_path, "mineflayer")
        file_path = os.path.abspath(os.path.dirname(__file__))
        return SubprocessMonitor(
            commands=[
                "node",
                U.f_join(file_path, "mineflayer/index.js"),
                str(server_port),
            ],
            name="mineflayer",
            ready_match=r"Server started on port (\d+)",
            log_path=U.f_join(self.log_path, "mineflayer"),
        )

    def get_mc_instance(self):
        print("Creating Minecraft server")
        U.f_mkdir(self.log_path, "minecraft")
        return MinecraftInstance(
            **self.azure_login,
            mineflayer=self.mineflayer,
            log_path=U.f_join(self.log_path, "minecraft"),
        )

    def _server_port_open(self):
        try:
            with socket.create_connection(("127.0.0.1", self.server_port), timeout=1):
                return True
        except OSError:
            return False

    def _listener_pids(self):
        if psutil is None:
            return []
        pids = set()
        try:
            for conn in psutil.net_connections(kind="inet"):
                laddr = getattr(conn, "laddr", None)
                if not laddr:
                    continue
                if getattr(laddr, "port", None) != self.server_port:
                    continue
                status = getattr(conn, "status", "")
                if status not in ("LISTEN", getattr(psutil, "CONN_LISTEN", "LISTEN")):
                    continue
                if conn.pid is not None:
                    pids.add(conn.pid)
        except Exception:
            return []
        return sorted(pids)

    def _kill_stale_port_listeners(self, exclude_pids=None, timeout_seconds=10):
        if psutil is None:
            return False
        excluded = {pid for pid in (exclude_pids or set()) if pid is not None}
        killed_any = False
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            listener_pids = [pid for pid in self._listener_pids() if pid not in excluded]
            if not listener_pids:
                return killed_any
            for pid in listener_pids:
                try:
                    proc = psutil.Process(pid)
                    if proc.is_running():
                        print(
                            f"Terminating stale mineflayer listener PID {pid} on port {self.server_port}"
                        )
                        proc.terminate()
                        killed_any = True
                except Exception:
                    pass
            time.sleep(0.5)
            for pid in listener_pids:
                try:
                    proc = psutil.Process(pid)
                    if proc.is_running():
                        proc.kill()
                        killed_any = True
                except Exception:
                    pass
            time.sleep(0.5)
        return killed_any

    def _ensure_mineflayer_process(self, allow_reuse=True, max_attempts=4):
        last_error = None
        for attempt in range(max_attempts):
            if allow_reuse and self._server_port_open():
                print(
                    f"Mineflayer bridge already listening on port {self.server_port}, reusing it"
                )
                return "reused"

            if not allow_reuse and self._server_port_open():
                self._kill_stale_port_listeners(exclude_pids=None)
                if self._server_port_open():
                    raise RuntimeError(
                        f"Mineflayer bridge port {self.server_port} is still occupied before restart"
                    )

            if self.mineflayer.is_running:
                return "running"

            delay_seconds = min(2**attempt, 8) if attempt else 0
            if delay_seconds:
                time.sleep(delay_seconds)
            print("Mineflayer process has exited, restarting")
            self.mineflayer.run()
            self.connected = False
            if self.mineflayer.is_running or self._wait_for_server_port_state(True, 15):
                if self.mineflayer.ready_line:
                    print(self.mineflayer.ready_line)
                return "started"
            last_error = RuntimeError("Mineflayer process failed to expose bridge port")
        raise last_error or RuntimeError("Mineflayer process failed to start")

    def _ensure_runtime_ready(self, allow_reuse=True):
        if self.mc_instance and not self.mc_instance.is_running:
            print("Starting Minecraft server")
            self.mc_instance.run()
            self.mc_port = self.mc_instance.port
            if self.reset_options is not None:
                self.reset_options["port"] = self.mc_instance.port
            print(f"Server started on port {self.mc_port}")
        return self._ensure_mineflayer_process(allow_reuse=allow_reuse)

    def check_process(self, force_start=False):
        self._ensure_runtime_ready(allow_reuse=True)

        if not force_start and self.connected:
            return None

        return self._start_bridge(self.reset_options, stop_on_error=True)

    def _start_bridge(self, reset_options, stop_on_error=True):
        request_retry = 0
        last_error = None
        while request_retry <= 10:
            if not self.mineflayer.is_running and not self._server_port_open():
                self._ensure_runtime_ready(allow_reuse=True)
            try:
                res = requests.post(
                    f"{self.server}/start",
                    json=reset_options,
                    timeout=self.request_timeout,
                )
            except requests.RequestException as exc:
                last_error = exc
                request_retry += 1
                time.sleep(1)
                continue
            if res.status_code != 200:
                self.connected = False
                self.server_paused = False
                raise RuntimeError(
                    f"Minecraft server reply with code {res.status_code}: {res.text}"
                )
            self.connected = True
            self.server_paused = False
            return res.json()

        raise RuntimeError(
            f"Mineflayer HTTP bridge did not accept /start in time: {last_error}"
        )

    def _restart_bridge_process(self):
        self.mineflayer.stop()
        if not self._wait_for_server_port_state(False, 10):
            self._kill_stale_port_listeners(exclude_pids=None)
            if not self._wait_for_server_port_state(False, 10):
                raise RuntimeError(
                    f"Mineflayer bridge port {self.server_port} stayed occupied after stop"
                )
        self.connected = False
        self.server_paused = False
        self._ensure_runtime_ready(allow_reuse=False)

    def _hard_reset(self, reset_options):
        hard_options = dict(reset_options)
        hard_options["reset"] = "hard"
        self.connected = False
        self.server_paused = False
        try:
            return self._start_bridge(hard_options, stop_on_error=True)
        except Exception as exc:
            if self._is_minecraft_unavailable_error(exc):
                raise
            self._restart_bridge_process()
            return self._start_bridge(hard_options, stop_on_error=True)

    def telemetry(self) -> Dict[str, Any] | None:
        if not self._server_port_open():
            return None
        try:
            res = requests.post(f"{self.server}/telemetry", json={}, timeout=5)
        except requests.RequestException:
            return None
        if res.status_code != 200:
            return None
        try:
            return res.json()
        except Exception:
            return None

    def set_wait_guard(self, enabled: bool) -> bool:
        if not self._server_port_open():
            return False
        try:
            res = requests.post(
                f"{self.server}/guard",
                json={"enabled": bool(enabled)},
                timeout=5,
            )
        except requests.RequestException:
            return False
        if res.status_code != 200:
            return False
        try:
            payload = res.json()
        except Exception:
            return False
        return bool(payload.get("enabled")) == bool(enabled)

    def _parse_event_timestamp(self, raw_value):
        if not isinstance(raw_value, str) or not raw_value:
            return None
        try:
            return datetime.fromisoformat(raw_value.replace("Z", "+00:00")).timestamp()
        except Exception:
            return None

    def _death_event_after(self, telemetry, started_at):
        if not isinstance(telemetry, dict):
            return None
        death_event = telemetry.get("lastDeathEvent") if isinstance(telemetry.get("lastDeathEvent"), dict) else None
        if not isinstance(death_event, dict):
            return None
        event_ts = self._parse_event_timestamp(death_event.get("respawn_observed_at"))
        if event_ts is None:
            event_ts = self._parse_event_timestamp(death_event.get("recorded_at"))
        if event_ts is None or event_ts < started_at:
            return None
        return death_event

    def _step_request_with_death_interrupt(self, data):
        started_at = time.time()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = executor.submit(
            requests.post,
            f"{self.server}/step",
            json=data,
            timeout=self.request_timeout,
        )
        try:
            while True:
                try:
                    return future.result(timeout=1.0)
                except concurrent.futures.TimeoutError:
                    telemetry = self.telemetry()
                    death_event = self._death_event_after(telemetry, started_at)
                    if death_event is not None:
                        future.cancel()
                        try:
                            self._restart_bridge_process()
                        except Exception:
                            pass
                        raise RuntimeError(
                            f"Minecraft step interrupted by death event: {death_event.get('death_message') or death_event.get('cause') or 'unknown cause'}"
                        )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    def step(
        self,
        code: str,
        programs: str = "",
    ) -> Tuple[ObsType, SupportsFloat, bool, bool, Dict[str, Any]]:
        if not self.has_reset:
            raise RuntimeError("Environment has not been reset yet")
        data = {
            "code": code,
            "programs": programs,
        }
        last_error = None
        self.set_wait_guard(False)
        for attempt in range(2):
            self.check_process(force_start=attempt > 0)
            self.unpause()
            try:
                res = self._step_request_with_death_interrupt(data)
            except requests.RequestException as exc:
                last_error = exc
            except RuntimeError as exc:
                last_error = exc
            else:
                if res.status_code == 200:
                    returned_data = res.json()
                    self.pause()
                    self.set_wait_guard(True)
                    return json.loads(returned_data)
                last_error = RuntimeError(
                    f"Failed to step Minecraft server: {res.status_code} / {res.text}"
                )
                if not self._is_minecraft_unavailable_error(last_error) or attempt >= 1:
                    self.set_wait_guard(True)
                    raise last_error
            self.connected = False
            self.server_paused = False
            if attempt >= 1:
                break
            self._restart_bridge_process()
        self.set_wait_guard(True)
        raise RuntimeError(f"Failed to step Minecraft server: {last_error}") from last_error

    def render(self):
        raise NotImplementedError("render is not implemented")

    def reset(
        self,
        *,
        seed=None,
        options=None,
    ) -> Tuple[ObsType, Dict[str, Any]]:
        if options is None:
            options = {}

        requested_mode = options.get("mode", "hard")
        if options.get("inventory", {}) and requested_mode != "hard":
            raise RuntimeError("inventory can only be set when options is hard")

        self.reset_options = {
            "port": self.mc_port,
            "reset": requested_mode,
            "inventory": options.get("inventory", {}),
            "equipment": options.get("equipment", []),
            "spread": options.get("spread", False),
            "waitTicks": options.get("wait_ticks", 5),
            "position": options.get("position", None),
        }

        self.unpause()
        try:
            if requested_mode == "soft" and self.connected and self.mineflayer.is_running:
                returned_data = self._start_bridge(dict(self.reset_options), stop_on_error=False)
            else:
                returned_data = self._hard_reset(self.reset_options)
        except Exception as exc:
            if self._is_minecraft_unavailable_error(exc):
                raise
            fallback_options = dict(self.reset_options)
            fallback_options["reset"] = "hard"
            self.reset_options = fallback_options
            returned_data = self._hard_reset(self.reset_options)

        self.has_reset = True
        # All the reset in step will be soft
        self.reset_options["reset"] = "soft"
        self.pause()
        self.set_wait_guard(True)
        return json.loads(returned_data)

    def close(self):
        self.unpause()
        if self.connected:
            res = requests.post(f"{self.server}/stop")
            if res.status_code == 200:
                self.connected = False
                self.server_paused = False
        if self.mc_instance:
            self.mc_instance.stop()
        self.mineflayer.stop()
        return not self.connected

    def pause(self):
        if self.mineflayer.is_running and not self.server_paused:
            res = requests.post(f"{self.server}/pause")
            if res.status_code == 200:
                self.server_paused = True
        return self.server_paused

    def unpause(self):
        if self.mineflayer.is_running and self.server_paused:
            res = requests.post(f"{self.server}/pause")
            if res.status_code == 200:
                self.server_paused = False
            else:
                print(res.json())
        return self.server_paused

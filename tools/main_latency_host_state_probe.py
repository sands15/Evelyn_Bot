"""Content-free stable Docker state probe for the independent launcher."""

from __future__ import annotations

from pathlib import Path

from tools.main_latency_host_lifecycle import (
    HostLifecycleError,
    MainLatencyHostLifecycle,
)


DOCKER_EXE = Path(r"C:\Program Files\Docker\Docker\resources\bin\docker.exe")
NVIDIA_SMI_EXE = Path(r"C:\Windows\System32\nvidia-smi.exe")


def main() -> int:
    try:
        state = MainLatencyHostLifecycle(
            DOCKER_EXE,
            NVIDIA_SMI_EXE,
        ).probe_docker_state()
    except HostLifecycleError:
        return 1
    print(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Run a program in a process of its own, with a time limit.

Compute-heavy steps (OCR, parsing) run in child processes, so the event loop stays free and a
step that hangs can be stopped. The child starts a new session; on timeout or cancellation the
whole process group is killed, including the programs it started (Tesseract, Ghostscript).
"""

import asyncio
import contextlib
import os
import signal
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta

# Output kept for error messages.
_TAIL = 4000


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str  # the last few thousand characters


async def run_process(
    args: Sequence[str],
    *,
    timeout: timedelta,
    env: Mapping[str, str] | None = None,
) -> Completed:
    """Run `args` and wait for it. TimeoutError after `timeout`; the process group is killed
    then, and also when the caller is cancelled."""
    process = await asyncio.create_subprocess_exec(
        *args,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
        env=None if env is None else {**os.environ, **env},
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(), timeout=timeout.total_seconds()
        )
    except TimeoutError:
        await _kill(process)
        raise TimeoutError(
            f"{os.path.basename(args[0])} did not finish within {timeout.total_seconds():g} s"
        ) from None
    except BaseException:
        await _kill(process)
        raise
    assert process.returncode is not None
    return Completed(
        returncode=process.returncode,
        stdout=stdout.decode(errors="replace"),
        stderr=stderr.decode(errors="replace")[-_TAIL:],
    )


async def _kill(process: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGKILL)
    # Reap the child; shielded, so a second cancellation does not leave a zombie behind.
    await asyncio.shield(process.wait())

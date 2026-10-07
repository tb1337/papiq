"""Adapters to the operating system: the system clock, child processes."""

from papiq.adapters.outbound.system.clock import SystemClock
from papiq.adapters.outbound.system.process import Completed, run_process

__all__ = ["Completed", "SystemClock", "run_process"]

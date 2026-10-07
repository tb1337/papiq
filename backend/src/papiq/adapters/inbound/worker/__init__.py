"""Inbound adapter: the worker service (jobs, event delivery, cleanup)."""

from papiq.adapters.inbound.worker.worker import Worker

__all__ = ["Worker"]

"""Failures that leave a durable run resumable instead of publishing an answer."""


class RecoverableRunError(RuntimeError):
    """Execution stopped before delivery; retry the same persisted request."""

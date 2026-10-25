"""Failures that leave a durable run resumable instead of publishing an answer."""


class RecoverableRunError(RuntimeError):
    """Execution stopped before delivery; retry the same persisted request."""


class RunPauseRequested(RuntimeError):
    """A durable user intent forbids starting work or publishing a candidate."""

    def __init__(self, row):
        super().__init__("Run pause requested; preserve the checkpoint without delivery")
        self.row = dict(row)
        self.run_id = row["run_id"]
        self.pause_revision = row["control_revision"]

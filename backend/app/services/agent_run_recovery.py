"""Bounded recovery of accepted durable runs after worker/process interruption."""

import asyncio
import logging

from fastapi import HTTPException

from app.models import AgentChatRequest
from app.repositories.agent_usage_repository import AgentUsageRepository
from app.services.agent_run_services import AgentRunServices

logger = logging.getLogger(__name__)


def recover_once():
    """Request recovery; the database execution lease elects the only worker."""
    from app.agents.runner.durable_runtime import get_runtime
    from app.routers.react_chat import cancel, start

    if get_runtime() is None:
        return 0
    services = AgentRunServices()
    usage = AgentUsageRepository(services.storage.database_url)
    attempted = 0
    for row in services.registry.recoverable_runs(limit=20, max_recoveries=3):
        run_id, owner_id = row["run_id"], row["owner_id"]
        try:
            if row["cancelled"]:
                cancel(run_id, owner_id)
                continue
            # A crash before admission has no accepted network identity to reuse.
            # Leave that reservation for the user's original POST, rather than
            # inventing an IP or skipping normal request admission.
            if usage.recovery_ip_hash(run_id, owner_id) is None:
                continue
            request = AgentChatRequest.model_validate_json(row["request_json"])
            start(request, None, owner_id)
            attempted += 1
        except HTTPException as error:
            logger.info("Run recovery deferred for %s: HTTP %s", run_id, error.status_code)
        except Exception:
            logger.exception("Run recovery failed for %s", run_id)
    return attempted


async def supervise_run_recovery(*, interval_seconds=30):
    """Lifespan-owned polling; stopping the poller never cancels a user run."""
    if interval_seconds <= 0:
        raise ValueError("Recovery interval must be positive")
    while True:
        try:
            await asyncio.to_thread(recover_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unable to scan resumable Agent runs")
        await asyncio.sleep(interval_seconds)

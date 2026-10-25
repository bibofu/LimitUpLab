"""Authenticated paging of immutable run evidence; GET never executes a tool."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.agents.result_paging import ReadResultRequest
from app.security import current_owner_id
from app.services.agent_result_reader import read_owned_result

router = APIRouter(prefix="/chat/runs")


@router.get("/{run_id}/results/{result_id}")
def result_page(run_id: str, result_id: str, response: Response,
                owner_id: Annotated[str, Depends(current_owner_id)],
                section: Literal["rows", "metadata"] = "rows",
                offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100),
                fields: Annotated[list[str] | None, Query()] = None,
                expected_hash: str | None = Query(default=None, min_length=64, max_length=64)):
    response.headers["Cache-Control"] = "private, no-store"
    try:
        request = ReadResultRequest(result_id=result_id, section=section, offset=offset, limit=limit,
                                    fields=fields, expected_hash=expected_hash)
        return read_owned_result(None, owner_id=owner_id, run_id=run_id, request=request)
    except KeyError as error:
        raise HTTPException(404, "Saved result not found") from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error

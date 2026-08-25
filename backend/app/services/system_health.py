"""Local system health checks for startup and frontend status."""

from __future__ import annotations

import os
import socket
from datetime import date, datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from app.agents.eval_runner import load_eval_cases, run_agent_eval_suite
from app.agents.query_contract_eval import (
    load_query_contract_eval_cases,
    run_query_contract_eval_suite,
)
from app.config import env_bool
from app.models import AgentSystemHealthResponse, LimitUpEvent
from app.repositories import SQLiteFirstBoardRepository
from app.services.analysis import latest_trade_date
from app.services.data_health import build_agent_data_health
from app.services.sample_data import SAMPLE_EVENTS


SYSTEM_HEALTH_VERSION = "agent-system-health-v1"
CN_TZ = ZoneInfo("Asia/Shanghai")


def build_agent_system_health(
    events: list[LimitUpEvent],
    *,
    first_board_repository: SQLiteFirstBoardRepository | None = None,
    run_offline_eval: bool = False,
) -> AgentSystemHealthResponse:
    """Build local runtime health status for development and demos."""

    now = datetime.now(CN_TZ)
    latest_date = latest_trade_date(events) if events else None
    expected_date, update_reason = expected_local_data_date(now)
    data_fresh = bool(latest_date and expected_date and latest_date >= expected_date)
    data_update_recommended = bool(expected_date and not data_fresh)

    data_health = build_agent_data_health(
        events=events,
        first_board_repository=first_board_repository or SQLiteFirstBoardRepository(),
        trade_date=latest_date or expected_date,
    )
    eval_total: int | None = None
    eval_failed: int | None = None
    eval_passed: bool | None = None
    if run_offline_eval:
        fixture_path = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "agent_eval_cases.json"
        suite = run_agent_eval_suite(
            cases=load_eval_cases(fixture_path),
            events=SAMPLE_EVENTS,
        )
        contract_fixture_path = (
            Path(__file__).resolve().parents[2]
            / "tests"
            / "fixtures"
            / "query_contract_v2_cases.json"
        )
        contract_suite = run_query_contract_eval_suite(
            load_query_contract_eval_cases(contract_fixture_path)
        )
        eval_total = suite.total + contract_suite.total
        eval_failed = suite.failed + contract_suite.failed
        eval_passed = suite.ok and contract_suite.ok

    llm_enabled = env_bool("LIMITUPLAB_LLM_ENABLED")
    llm_provider_configured = bool(
        os.getenv("DEEPSEEK_API_KEY", "").strip()
        or os.getenv("OPENAI_API_KEY", "").strip()
    )
    proxy_warning = _proxy_warning()
    warnings = list(data_health.warnings)
    if data_update_recommended:
        warnings.append(
            f"Local data is older than expected {expected_date.isoformat() if expected_date else 'unknown'}."
        )
    if llm_enabled and not llm_provider_configured:
        warnings.append("LLM is enabled but no API key is configured.")
    if proxy_warning:
        warnings.append(proxy_warning)
    if eval_passed is False:
        warnings.append("Offline Agent eval has failing cases.")

    status = _overall_status(
        data_health_status=data_health.status,
        data_fresh=data_fresh,
        llm_enabled=llm_enabled,
        llm_provider_configured=llm_provider_configured,
        offline_eval_passed=eval_passed,
    )
    return AgentSystemHealthResponse(
        status=status,
        current_date=now.date(),
        current_time=now.strftime("%H:%M:%S"),
        latest_local_trade_date=latest_date,
        expected_data_date=expected_date,
        data_fresh=data_fresh,
        data_update_recommended=data_update_recommended,
        data_update_reason=update_reason,
        llm_enabled=llm_enabled,
        llm_provider_configured=llm_provider_configured,
        llm_model=os.getenv("LIMITUPLAB_LLM_MODEL") or None,
        proxy_configured=bool(_current_proxy()),
        proxy_warning=proxy_warning,
        offline_eval_passed=eval_passed,
        offline_eval_total=eval_total,
        offline_eval_failed=eval_failed,
        data_health=data_health,
        warnings=warnings,
        generated_by=SYSTEM_HEALTH_VERSION,
    )


def expected_local_data_date(now: datetime | None = None) -> tuple[date | None, str]:
    """Return the date local data should cover before startup."""

    current = now or datetime.now(CN_TZ)
    if current.weekday() >= 5:
        previous = current.date()
        while previous.weekday() >= 5:
            previous -= timedelta(days=1)
        return previous, "Weekend: latest previous weekday data is expected."
    if current.time() >= time(15, 30):
        return current.date(), "Weekday after close: today's after-close data is expected."

    previous = current.date() - timedelta(days=1)
    while previous.weekday() >= 5:
        previous -= timedelta(days=1)
    return previous, "Before close: previous weekday data is acceptable."


def _current_proxy() -> str:
    return (
        os.getenv("HTTPS_PROXY", "").strip()
        or os.getenv("HTTP_PROXY", "").strip()
        or os.getenv("ALL_PROXY", "").strip()
    )


def _proxy_warning() -> str | None:
    proxy = _current_proxy()
    if not proxy:
        return None
    parsed = urlparse(proxy)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.port is None:
        return None
    try:
        with socket.create_connection((parsed.hostname, parsed.port), timeout=0.2):
            return None
    except OSError:
        return f"Configured local proxy {parsed.hostname}:{parsed.port} is unreachable."


def _overall_status(
    *,
    data_health_status: str,
    data_fresh: bool,
    llm_enabled: bool,
    llm_provider_configured: bool,
    offline_eval_passed: bool | None,
) -> str:
    if data_health_status == "missing":
        return "missing"
    if llm_enabled and not llm_provider_configured:
        return "partial"
    if not data_fresh:
        return "partial"
    if offline_eval_passed is False:
        return "partial"
    if data_health_status == "partial":
        return "partial"
    return "healthy"

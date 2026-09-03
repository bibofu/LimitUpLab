"""Run a Markdown question bank through the production chat Agent.

The runner keeps one in-memory conversation per Markdown section, records the
complete user-visible answer and latency breakdown, and checkpoints JSON plus
Markdown reports after every question so a long live-LLM run can be resumed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.agents.chat import answer_first_board_chat, template_answer_override
from app.config import configure_runtime_environment
from app.models import AgentChatRequest, AgentRun, ChatSessionMessage
from app.repositories import SQLiteFirstBoardRepository, get_limit_up_repository
from app.services.llm_provider import DisabledLLMProvider, get_llm_provider


@dataclass(frozen=True)
class Question:
    """One numbered question and its expected capability annotation."""

    number: int
    text: str
    expected_capability: str
    section: str


def parse_question_bank(path: Path) -> list[Question]:
    """Parse numbered questions and the following Chinese capability labels."""

    import re

    question_pattern = re.compile(r"^(\d{1,3})\.\s+(.+?)\s*$")
    capability_pattern = re.compile(r"^\s*【(.+?)】\s*$")
    section = "未分组"
    pending: tuple[int, str, str] | None = None
    questions: list[Question] = []

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.rstrip()
        if line.startswith("## 附"):
            break
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        question_match = question_pattern.match(line)
        if question_match:
            if pending is not None:
                number, text, pending_section = pending
                questions.append(Question(number, text, "", pending_section))
            pending = (
                int(question_match.group(1)),
                question_match.group(2).strip(),
                section,
            )
            continue
        capability_match = capability_pattern.match(line)
        if capability_match and pending is not None:
            number, text, pending_section = pending
            questions.append(
                Question(
                    number=number,
                    text=text,
                    expected_capability=capability_match.group(1).strip(),
                    section=pending_section,
                )
            )
            pending = None

    if pending is not None:
        number, text, pending_section = pending
        questions.append(Question(number, text, "", pending_section))

    numbers = [question.number for question in questions]
    if numbers != list(range(1, len(questions) + 1)):
        raise ValueError(f"question IDs must be continuous from 1; got {numbers}")
    return questions


def _planner_capabilities(response: Any) -> list[str]:
    for trace in response.tool_results:
        if trace.name == "llm_tool_planner":
            capabilities = trace.input.get("capabilities") or []
            return [str(item) for item in capabilities if isinstance(item, str)]
    return []


def _conversation_messages(
    *,
    session_id: str,
    question_number: int,
    question: str,
    answer: str,
    capabilities: list[str],
) -> list[ChatSessionMessage]:
    created_at = datetime.now(timezone.utc)
    return [
        ChatSessionMessage(
            message_id=f"question-{question_number}-user",
            session_id=session_id,
            role="user",
            content=question,
            created_at=created_at,
        ),
        ChatSessionMessage(
            message_id=f"question-{question_number}-assistant",
            session_id=session_id,
            role="assistant",
            content=answer,
            metadata={"capabilities": capabilities},
            created_at=created_at,
        ),
    ]


def _nearest_rank(values: list[int], proportion: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(len(ordered) * proportion + 0.999999)))
    return ordered[rank - 1]


def build_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    """Build aggregate latency and outcome metrics for completed attempts."""

    successes = [item for item in results if item.get("status") == "success"]
    failures = [item for item in results if item.get("status") != "success"]
    durations = [int(item["wall_duration_ms"]) for item in successes]
    return {
        "attempted": len(results),
        "succeeded": len(successes),
        "failed": len(failures),
        "success_rate": round(len(successes) / len(results), 4) if results else 0.0,
        "total_wall_duration_ms": sum(durations),
        "average_wall_duration_ms": round(statistics.mean(durations)) if durations else None,
        "p50_wall_duration_ms": _nearest_rank(durations, 0.50),
        "p95_wall_duration_ms": _nearest_rank(durations, 0.95),
        "min_wall_duration_ms": min(durations) if durations else None,
        "max_wall_duration_ms": max(durations) if durations else None,
    }


def _format_duration(duration_ms: int | None) -> str:
    if duration_ms is None:
        return "—"
    return f"{duration_ms / 1000:.3f}s"


def render_markdown(report: dict[str, Any]) -> str:
    """Render a human-readable report with every complete Agent answer."""

    summary = report["summary"]
    lines = [
        "# LimitUpLab Agent 100 题实测记录",
        "",
        f"- 开始时间：{report['started_at']}",
        f"- 最后更新：{report['updated_at']}",
        f"- 模式：{report['mode']}",
        f"- 模型：{report['model']}",
        f"- 输入文件：{report['question_bank']}",
        f"- 已执行：{summary['attempted']} / {report['question_count']}",
        f"- 成功 / 失败：{summary['succeeded']} / {summary['failed']}",
        f"- 平均耗时：{_format_duration(summary['average_wall_duration_ms'])}",
        f"- P50 / P95：{_format_duration(summary['p50_wall_duration_ms'])} / "
        f"{_format_duration(summary['p95_wall_duration_ms'])}",
        f"- 成功题累计墙钟耗时：{_format_duration(summary['total_wall_duration_ms'])}",
        "",
        "| 题号 | 场景 | 状态 | 意图 | 工具 | 墙钟耗时 | Agent 内部耗时 |",
        "|---:|---|---|---|---|---:|---:|",
    ]
    for item in report["results"]:
        tools = ", ".join(item.get("tool_calls") or []) or "—"
        lines.append(
            f"| {item['number']} | {item['section'].replace('|', '\\|')} | "
            f"{item['status']} | {item.get('intent') or '—'} | "
            f"{tools.replace('|', '\\|')} | {_format_duration(item.get('wall_duration_ms'))} | "
            f"{_format_duration((item.get('performance') or {}).get('total_duration_ms'))} |"
        )

    current_section = None
    for item in report["results"]:
        if item["section"] != current_section:
            current_section = item["section"]
            lines.extend(["", f"## {current_section}"])
        lines.extend(
            [
                "",
                f"### {item['number']}. {item['question']}",
                "",
                f"- 预期能力：{item['expected_capability'] or '未标注'}",
                f"- 状态：{item['status']}",
                f"- 意图：{item.get('intent') or '—'}",
                f"- 工具：{', '.join(item.get('tool_calls') or []) or '—'}",
                f"- 墙钟耗时：{_format_duration(item.get('wall_duration_ms'))}",
            ]
        )
        performance = item.get("performance") or {}
        if performance:
            lines.append(
                "- 内部耗时："
                f"Planner {_format_duration(performance.get('planner_duration_ms'))}，"
                f"工具 {_format_duration(performance.get('tool_duration_ms'))}，"
                f"回答 {_format_duration(performance.get('answer_duration_ms'))}，"
                f"合计 {_format_duration(performance.get('total_duration_ms'))}"
            )
        if item.get("warnings"):
            lines.append(f"- 警告：{'；'.join(item['warnings'])}")
        lines.extend(["", "Agent 回答：", ""])
        if item["status"] == "success":
            lines.append(item.get("answer") or "（空回答）")
        else:
            lines.append(f"运行失败：{item.get('error') or '未知错误'}")
    lines.append("")
    return "\n".join(lines)


def save_report(report: dict[str, Any], json_path: Path, markdown_path: Path) -> None:
    """Atomically checkpoint both report formats."""

    report["updated_at"] = datetime.now(timezone.utc).isoformat()
    report["summary"] = build_summary(report["results"])
    json_tmp = json_path.with_suffix(json_path.suffix + ".tmp")
    markdown_tmp = markdown_path.with_suffix(markdown_path.suffix + ".tmp")
    json_tmp.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    markdown_tmp.write_text(render_markdown(report), encoding="utf-8")
    json_tmp.replace(json_path)
    markdown_tmp.replace(markdown_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a Markdown question bank through the production chat Agent."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=PROJECT_ROOT / "testQuestion.md",
        help="Markdown question bank path.",
    )
    parser.add_argument("--start", type=int, default=1, help="First question number.")
    parser.add_argument("--end", type=int, default=100, help="Last question number.")
    parser.add_argument(
        "--output-stem",
        type=Path,
        default=BACKEND_ROOT / "data" / "agent_question_bank_eval_live",
        help="Output path without extension.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from an existing JSON report and rebuild section context.",
    )
    parser.add_argument(
        "--template-answer",
        action="store_true",
        help="Use deterministic final answers while retaining the configured planner.",
    )
    args = parser.parse_args()

    if args.start < 1 or args.end < args.start:
        parser.error("expected 1 <= --start <= --end")
    configure_runtime_environment()
    questions = parse_question_bank(args.input.resolve())
    if len(questions) != 100:
        parser.error(f"expected exactly 100 questions, found {len(questions)}")
    selected = [item for item in questions if args.start <= item.number <= args.end]
    if not selected:
        parser.error("selected question range is empty")

    provider = get_llm_provider()
    if isinstance(provider, DisabledLLMProvider):
        parser.error("the live LLM provider is disabled or has no configured API key")

    output_stem = args.output_stem.resolve()
    output_stem.parent.mkdir(parents=True, exist_ok=True)
    json_path = output_stem.with_suffix(".json")
    markdown_path = output_stem.with_suffix(".md")
    if args.resume and json_path.exists():
        report = json.loads(json_path.read_text(encoding="utf-8"))
    else:
        report = {
            "schema_version": "agent-question-bank-eval-v1",
            "question_bank": str(args.input.resolve()),
            "question_count": len(questions),
            "selected_range": [args.start, args.end],
            "mode": "template-answer" if args.template_answer else "live-llm",
            "model": getattr(provider, "model", type(provider).__name__),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "summary": {},
            "results": [],
        }

    completed_numbers = {int(item["number"]) for item in report["results"]}
    conversations: dict[str, list[ChatSessionMessage]] = {}
    recent_runs: dict[str, list[AgentRun]] = {}
    for item in report["results"]:
        if item.get("status") != "success":
            continue
        section = str(item["section"])
        session_id = f"question-bank-{questions[int(item['number']) - 1].number}-{section[:16]}"
        conversations.setdefault(section, []).extend(
            _conversation_messages(
                session_id=session_id,
                question_number=int(item["number"]),
                question=str(item["question"]),
                answer=str(item.get("answer") or ""),
                capabilities=list(item.get("planner_capabilities") or []),
            )
        )

    events = get_limit_up_repository().list_events()
    repository = SQLiteFirstBoardRepository()
    print(
        f"Loaded {len(questions)} questions; running {args.start}-{args.end} "
        f"with {report['model']}."
    )
    print(f"JSON: {json_path}")
    print(f"Markdown: {markdown_path}")

    for question in selected:
        if question.number in completed_numbers:
            print(f"[{question.number:03d}/100] skipped (already recorded)", flush=True)
            continue
        session_id = f"question-bank-section-{abs(hash(question.section))}"
        request = AgentChatRequest(session_id=session_id, message=question.text)
        started_at = datetime.now(timezone.utc)
        started = perf_counter()
        print(f"[{question.number:03d}/100] {question.text}", flush=True)
        try:
            with template_answer_override(args.template_answer):
                response = answer_first_board_chat(
                    request=request,
                    events=events,
                    repository=repository,
                    recent_runs=recent_runs.get(question.section, []),
                    conversation_messages=conversations.get(question.section, []),
                    llm_provider=provider,
                )
            wall_duration_ms = round((perf_counter() - started) * 1000)
            finished_at = datetime.now(timezone.utc)
            performance = response.performance.model_dump(mode="json")
            item = {
                "number": question.number,
                "section": question.section,
                "question": question.text,
                "expected_capability": question.expected_capability,
                "status": "success",
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "wall_duration_ms": wall_duration_ms,
                "intent": response.intent,
                "generated_by": response.generated_by,
                "tool_calls": list(response.tool_calls),
                "planner_capabilities": _planner_capabilities(response),
                "warnings": list(response.warnings),
                "references": list(response.references),
                "performance": performance,
                "answer": response.answer,
            }
            report["results"].append(item)
            conversations.setdefault(question.section, []).extend(
                _conversation_messages(
                    session_id=session_id,
                    question_number=question.number,
                    question=question.text,
                    answer=response.answer,
                    capabilities=item["planner_capabilities"],
                )
            )
            run_id = f"question-bank-{question.number}"
            recent_runs.setdefault(question.section, []).insert(
                0,
                AgentRun(
                    run_id=run_id,
                    session_id=session_id,
                    run_type="agent_chat",
                    status="success",
                    intent=response.intent,
                    tool_calls=list(response.tool_calls),
                    input_json=request.model_dump(mode="json"),
                    output_json=response.model_dump(mode="json"),
                    started_at=started_at,
                    finished_at=finished_at,
                ),
            )
            recent_runs[question.section] = recent_runs[question.section][:10]
            print(
                f"    ok intent={response.intent} wall={wall_duration_ms}ms "
                f"agent={performance['total_duration_ms']}ms",
                flush=True,
            )
        except Exception as error:  # noqa: BLE001 - one failed case must not stop the bank
            wall_duration_ms = round((perf_counter() - started) * 1000)
            report["results"].append(
                {
                    "number": question.number,
                    "section": question.section,
                    "question": question.text,
                    "expected_capability": question.expected_capability,
                    "status": "error",
                    "started_at": started_at.isoformat(),
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "wall_duration_ms": wall_duration_ms,
                    "error_type": type(error).__name__,
                    "error": str(error),
                    "intent": None,
                    "tool_calls": [],
                    "warnings": [],
                    "performance": {},
                    "answer": "",
                }
            )
            print(f"    error after {wall_duration_ms}ms: {error}", flush=True)
        report["results"].sort(key=lambda item: int(item["number"]))
        save_report(report, json_path, markdown_path)

    summary = report["summary"]
    print(
        f"Finished: {summary['succeeded']} succeeded, {summary['failed']} failed, "
        f"average={summary['average_wall_duration_ms']}ms, "
        f"p95={summary['p95_wall_duration_ms']}ms."
    )


if __name__ == "__main__":
    main()

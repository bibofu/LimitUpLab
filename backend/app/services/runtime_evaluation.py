"""Deterministic runtime contracts over real PostgreSQL, with no external model."""

import asyncio
from contextlib import closing, contextmanager
from copy import deepcopy
from importlib.metadata import version
import json
from math import ceil
from statistics import mean
import sys
from time import perf_counter
from uuid import uuid4

from langchain_core.messages import AIMessage
import sqlalchemy as sa

from app.agents.context_builder import build_context, estimate_tokens
from app.agents.result_paging import ReadResultRequest, model_observation, read_result_page
from app.agents.run_lifecycle import RunControl, RunLifecycle
from app.agents.runner.adapter import _response, _traces
from app.agents.runner.durable_runtime import DurableRuntime
from app.agents.runner.runner import AgentRunner
from app.agents.runtime_errors import RecoverableRunError
from app.agents.tool_context import format_tool_observation
from app.agents.tools import AgentToolRegistry, ToolResult
from app.database import connect
from app.models import AgentChatRequest, ChatSessionMessage
from app.repositories import ChatSessionRepository, FirstBoardRepository
from app.repositories.long_term_memory_repository import LongTermMemoryRepository
from app.services.memory_retrieval import retrieve_memories
from app.storage.isolated import isolated_database_url
from app.storage.postgres import get_engine
from app.storage.schema import HEAD_REVISION, upgrade_schema

EVALUATION_VERSION = "runtime-contract-evaluation-v1"
WORLD_VERSION = "synthetic-runtime-world-v1"
CASES = ("concept", "list", "ordinal", "memory_cross_session", "interrupted_recovery")
TABLES = ("checkpoints", "checkpoint_blobs", "checkpoint_writes", "agent_tool_results", "chat_messages", "react_events")
ROWS = [{"symbol": "600001", "name": "样本甲", "close": 10.0},
        {"symbol": "000002", "name": "样本乙", "close": 20.0},
        {"symbol": "300003", "name": "样本丙", "close": 30.0}]
LIST_ANSWER = "| 股票代码 |\n|---|\n| 000002 |\n| 600001 |\n| 300003 |"


def call(name, args, call_id="call-one"):
    return AIMessage(content="", tool_calls=[{"id": call_id, "name": name, "args": args}])


def review(query=None, *, reference_intent=None):
    arguments = {"decision": "allow", "signals": [], "reason": "固定评测研究请求",
                 "reference_intent": reference_intent or {"kind": "none"}}
    if query:
        arguments["memory_intent"] = {"action": "save", "quote": query, "memory_type": "preference",
                                      "key": "response_style", "value": "concise"}
    return call("submit_input_security_review", arguments, "review")


class ScriptedModel:
    model = WORLD_VERSION

    def __init__(self, responses):
        self.responses, self.inputs, self.estimated_tokens = list(responses), [], []

    def generate_messages(self, messages, tools, **_options):
        self.inputs.append(messages)
        self.estimated_tokens.append(sum(estimate_tokens(item) for item in messages) + estimate_tokens(tools))
        if not self.responses:
            raise AssertionError("Fixed model script exhausted; no external fallback exists")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class FixedTools(AgentToolRegistry):
    @property
    def enabled_tool_names(self):
        return frozenset({"limit_up_events"})

    def __init__(self, url):
        super().__init__(events=[], first_board_repository=FirstBoardRepository(url), hithink_collector=object())
        self.actual_calls = 0

    def limit_up_events(self, **arguments):
        self.actual_calls += 1
        return ToolResult(name="limit_up_events", input=arguments, summary="固定评测三条样本",
                          output={"events": deepcopy(ROWS), "row_count": 3,
                                  "trade_date": "2026-09-01", "source": "fixture://" + WORLD_VERSION})


@contextmanager
def evaluation_schema(raw_url):
    """The only destructive action is removal of the random schema we created."""
    base = isolated_database_url(raw_url)
    schema = "limituplab_eval_" + uuid4().hex
    url = base.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(hide_password=False)
    admin = sa.create_engine(base, poolclass=sa.pool.NullPool, hide_parameters=True)
    engine = sa.create_engine(url, poolclass=sa.pool.NullPool, hide_parameters=True)
    created = False
    try:
        with admin.begin() as db:
            db.execute(sa.schema.CreateSchema(schema))
        created = True
        upgrade_schema(engine)
        yield url
    finally:
        engine.dispose()
        try:
            if created:
                get_engine(url).dispose()
                with admin.begin() as db:
                    db.execute(sa.schema.DropSchema(schema, cascade=True))
        finally:
            admin.dispose()


def storage_snapshot(url):
    result = {}
    with closing(connect(url)) as db:
        for table in TABLES:
            count, logical = db.execute(f"SELECT count(*),coalesce(sum(pg_column_size(t)),0) FROM {table} t").fetchone()
            allocated = db.execute("SELECT pg_total_relation_size(?::regclass)", (table,)).fetchone()[0]
            result[table] = {"rows": int(count), "logical_row_bytes": int(logical), "allocated_relation_bytes": int(allocated)}
    return result


class Trial:
    def __init__(self, url):
        self.url, self.owner = url, "eval-owner-" + uuid4().hex
        self.lifecycle, self.chats = RunLifecycle(url), ChatSessionRepository(url)
        self.tools, self.models, self.controls = FixedTools(url), [], []
        self.session = self.chats.create_session(owner_id=self.owner).session_id
        self.deliveries = []

    def start(self, query, responses):
        history = self.chats.get_session(self.session, owner_id=self.owner).messages
        request = AgentChatRequest(session_id=self.session, message_id=uuid4().hex, message=query)
        row, _ = self.lifecycle.create(request, self.owner)
        lease = self.lifecycle.claim(row["run_id"])
        row = self.lifecycle.services.registry.mark_running(row["run_id"], self.owner, execution=lease)
        control = RunControl(self.lifecycle, row, lease)
        self.controls.append(control)
        self.chats.append_message(ChatSessionMessage(message_id="user_" + row["run_id"], session_id=self.session,
            role="user", content=query, run_id=row["run_id"], created_at=row["started_at"]), owner_id=self.owner)
        model = ScriptedModel(responses)
        self.models.append(model)
        return request, control, AgentRunner(request, self.tools, model, history)

    def deliver(self, request, control, result):
        response = _response(request, result.answer, result.status, result.stop_reason,
                             tool_calls=result.tool_calls, tool_results=_traces(result)).model_copy(update={
            "memory_action": result.memory_action, "memory_owner_revision": result.memory_owner_revision,
            "memory_context_fingerprint": result.memory_context_fingerprint, "memory_context_used": result.memory_context_used})
        final = self.lifecycle.services.delivery.commit(control.row["run_id"], self.owner,
            response.model_dump(mode="json"), execution=control.execution)
        self.deliveries.append(final)
        control.execution.release()
        return final

    async def turn(self, runtime, query, responses):
        request, control, runner = self.start(query, responses)
        return self.deliver(request, control, await runtime.run(runner, control))

    def close(self):
        for control in self.controls:
            control.execution.release()


def context_payload(messages):
    return json.loads(messages[1].content.split("\n", 1)[1])


async def scenario(url, case):
    trial = Trial(url)
    runtime = await DurableRuntime.open(url)
    recovered = None
    try:
        if case == "concept":
            await trial.turn(runtime, "什么是首板", [review(), AIMessage(content="首板是连续涨停中的首个涨停板。")])
            contract = trial.tools.actual_calls == 0
        elif case in {"list", "ordinal"}:
            await trial.turn(runtime, "列出固定样本", [review(), call("limit_up_events", {"recent_trade_days": 1}), AIMessage(content=LIST_ANSWER)])
            contract = trial.tools.actual_calls == 1
            if case == "ordinal":
                await trial.turn(runtime, "第二只股票代码是什么", [review(reference_intent={
                    "kind": "entities", "quote": "第二只", "ordinal": 2}), AIMessage(content="第二只为600001样本甲。")])
                contract = contract and context_payload(trial.models[-1].inputs[-1])["resolved_reference"]["symbols"] == ["600001"]
        elif case == "memory_cross_session":
            query = "请长期记住：回答保持简洁"
            await trial.turn(runtime, query, [review(query)])
            trial.session = trial.chats.create_session(owner_id=trial.owner).session_id
            await trial.turn(runtime, "什么是首板", [review(), AIMessage(content="首板是连续涨停中的首个涨停板。")])
            contract = context_payload(trial.models[-1].inputs[-1])["long_term_memories"][0]["value"] == "concise"
        else:
            request, control, runner = trial.start("列出固定样本", [review(), call("limit_up_events", {"recent_trade_days": 1}),
                                                                   ConnectionError("injected model interruption")])
            try:
                await runtime.run(runner, control)
                raise AssertionError("Recovery case did not interrupt")
            except RecoverableRunError:
                trial.lifecycle.services.registry.mark_interrupted(control.row["run_id"], trial.owner, execution=control.execution)
            await runtime.pool.close()
            control.execution.release()
            control.execution = trial.lifecycle.claim(control.row["run_id"])
            control.row = trial.lifecycle.services.registry.mark_running(control.row["run_id"], trial.owner, execution=control.execution)
            runtime = await DurableRuntime.open(url)
            resumed = ScriptedModel([AIMessage(content=LIST_ANSWER)])
            trial.models.append(resumed)
            final = await runtime.run(AgentRunner(request, trial.tools, resumed), control)
            trial.deliver(request, control, final)
            recovered = final.status == "answered" and trial.tools.actual_calls == 1 and len(resumed.inputs) == 1
            contract = recovered
        started = perf_counter()
        recalled = retrieve_memories(LongTermMemoryRepository(url), owner_id=trial.owner, query="解释首板")
        retrieval_ms = (perf_counter() - started) * 1000
        return {"contract_pass": bool(contract), "delivery_success": all(r["task_status"] == "complete" for r in trial.deliveries),
                "turn_count": len(trial.deliveries), "recovery_success": recovered,
                "actual_business_tool_calls": trial.tools.actual_calls,
                "scripted_model_calls": sum(len(model.inputs) for model in trial.models),
                "estimated_model_input_tokens": sum(sum(model.estimated_tokens) for model in trial.models),
                "memory_retrieval_probe_ms": round(retrieval_ms, 3), "memory_probe_hits": len(recalled.items)}
    finally:
        await runtime.pool.close()
        trial.close()


def representation_comparison():
    value = {"name": "fixture_rows", "status": "ok", "source": "fixture://" + WORLD_VERSION,
             "field_definitions": {"rows": {"symbol": "合成代码", "close": "合成数值"}},
             "rows": [{"symbol": str(600000 + i), "close": i / 10, "name": "合成样本",
                       "source": "fixture://fixed-long-source", "trade_date": "2026-09-01"} for i in range(180)]}
    manifest = model_observation(value, "fixture-result", max_tokens=1500)
    pages = [read_result_page(value, ReadResultRequest(result_id="fixture-result", offset=i, limit=50)) for i in range(0, 180, 50)]
    return {"scope": "Same synthetic receipt; page total includes every row, no fields projected away",
            "rows": 180, "full_json_tokens": estimate_tokens(value),
            "lossless_tokens": estimate_tokens(format_tool_observation(value)),
            "paged_initial_tokens": estimate_tokens(manifest), "page_count": len(pages),
            "paged_all_rows_tokens_sum": estimate_tokens(manifest) + sum(estimate_tokens(format_tool_observation(p)) for p in pages),
            "all_rows_roundtrip": [row for page in pages for row in page["rows"]] == value["rows"]}


def memory_ablation(url):
    owner = "ablation-" + uuid4().hex
    repository = LongTermMemoryRepository(url)
    repository.put(owner, {"memory_type": "preference", "key": "response_style", "value": "concise",
                           "source_user_message": "显式保存评测偏好"}, "save")
    retrieval = retrieve_memories(repository, owner_id=owner, query="解释首板")
    conversation = {"active_constraints": ["只看主板"], "memory_context_fingerprint": retrieval.context_fingerprint}
    rows = []
    for mode in ("no_memory", "short_term", "short_and_long_term"):
        built = build_context(system_prompt="固定研究规则", query="解释首板", working_messages=[],
                              conversation={} if mode == "no_memory" else conversation,
                              long_term_memory=retrieval if mode == "short_and_long_term" else None)
        payload = context_payload(built.messages)
        rows.append({"mode": mode, "session_constraint_present": payload.get("active_constraints") == ["只看主板"],
                     "cross_session_preference_present": bool(payload.get("long_term_memories")),
                     "estimated_input_tokens": built.diagnostics["estimated_input_tokens"]})
    return {"scope": "Fixed context availability only; same no/short/long modes as memory ablation tests", "rows": rows}


def percentile(values, percentile):
    return round(sorted(values)[max(0, ceil(len(values) * percentile / 100) - 1)], 3)


def evaluate(raw_url, *, trials=3):
    if type(trials) is not int or not 1 <= trials <= 100:
        raise ValueError("trials must be an integer between 1 and 100")
    samples = []
    with evaluation_schema(raw_url) as url:
        for index in range(trials):
            for case in CASES:
                before, started = storage_snapshot(url), perf_counter()
                try:
                    outcome = asyncio.run(scenario(url, case), loop_factory=asyncio.SelectorEventLoop)
                except Exception as error:
                    outcome = {"contract_pass": False, "delivery_success": False, "error_type": type(error).__name__}
                elapsed, after = (perf_counter() - started) * 1000, storage_snapshot(url)
                samples.append({"case": case, "trial": index + 1, **outcome, "end_to_end_ms": round(elapsed, 3),
                                "storage_growth": {table: {key: after[table][key] - before[table][key] for key in before[table]} for table in TABLES}})
        ablation = memory_ablation(url)
    grouped = {}
    for case in CASES:
        rows = [row for row in samples if row["case"] == case]
        grouped[case] = {"samples": len(rows), "contract_success_rate": mean(row["contract_pass"] for row in rows),
                         "delivery_success_rate": mean(row["delivery_success"] for row in rows),
                         "recovery_success_rate": mean(bool(row.get("recovery_success")) for row in rows) if case == "interrupted_recovery" else None,
                         "actual_business_tool_calls_total": sum(row.get("actual_business_tool_calls", 0) for row in rows),
                         "p50_end_to_end_ms": percentile([row["end_to_end_ms"] for row in rows], 50),
                         "p95_end_to_end_ms": percentile([row["end_to_end_ms"] for row in rows], 95)}
    return {"evaluation_version": EVALUATION_VERSION, "case_version": WORLD_VERSION, "schema_version": HEAD_REVISION,
            "software": {"python": sys.version.split()[0], "langgraph": version("langgraph"),
                         "checkpoint_postgres": version("langgraph-checkpoint-postgres")},
            "scope": "deterministic runtime contracts, not LLM accuracy or provider billing", "external_model_calls": 0,
            "sample_count": len(samples), "trials": trials, "schema_cleaned": True,
            "metric_definitions": {"end_to_end_ms": "Case setup, pool open, all graph turns, delivery and cleanup; excludes schema migration",
                "estimated_model_input_tokens": "Sum of conservative ContextBuilder estimates for messages and definitions on each scripted call",
                "memory_retrieval_probe_ms": "One real repository/retrieval call after the scenario; not all internal graph retrieval calls",
                "storage_growth": "Rows and pg_column_size sum plus allocated table/index/TOAST bytes; storage growth proxies, not IO operations or WAL bytes",
                "percentiles": "Nearest-rank P50/P95 per case; small samples are descriptive only",
                "recovery_success": "Interrupted graph resumed with a new runtime and lease, no repeated business tool; not an OS crash benchmark"},
            "baseline": "No comparable historical production baseline; no production speedup claim",
            "cases": grouped, "samples": samples, "representations": representation_comparison(), "memory_ablation": ablation}

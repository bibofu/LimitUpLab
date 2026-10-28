import unittest
from contextlib import contextmanager
from unittest.mock import patch

from app.config import LangfuseSettings
from app.services import agent_tracing
from app.services.agent_tracing import (
    LangfuseTracer,
    NullTracer,
    configure_agent_tracer,
    get_agent_tracer,
    mask_trace_data,
    shutdown_agent_tracer,
    usage_details,
)


ACTIVE_SETTINGS = LangfuseSettings(
    enabled=True,
    host="https://us.cloud.langfuse.com",
    public_key="pk-test",
    secret_key="sk-test",
    sample_rate=1.0,
    environment="test",
)


@contextmanager
def _span_context(span):
    yield span


class _FakeSpan:
    def __init__(self) -> None:
        self.updates: list[dict] = []

    def update(self, **kwargs):
        self.updates.append(kwargs)
        return self


class _FakeClient:
    def __init__(self) -> None:
        self.observation_calls: list[dict] = []
        self.spans: list[_FakeSpan] = []
        self.fail_observation = False
        self.flushed = False
        self.shutdown_called = False
        self.scores: list[dict] = []

    def create_trace_id(self, seed=None):
        return f"trace-{seed}"

    def start_as_current_observation(self, **kwargs):
        self.observation_calls.append(kwargs)
        if self.fail_observation:
            raise RuntimeError("collector unavailable")
        span = _FakeSpan()
        self.spans.append(span)
        return _span_context(span)

    def get_trace_url(self, trace_id=None):
        return f"https://example.test/trace/{trace_id}"

    def create_score(self, **kwargs):
        self.scores.append(kwargs)
        return {"id": "score-1", **kwargs}

    def flush(self):
        self.flushed = True

    def shutdown(self):
        self.shutdown_called = True


class _FakePropagate:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    @contextmanager
    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        yield


class AgentTracingTest(unittest.TestCase):
    def setUp(self) -> None:
        agent_tracing._tracer = NullTracer()

    def tearDown(self) -> None:
        agent_tracing._tracer = NullTracer()

    # Regression scenario: every tracer block works without a collector.
    def test_null_tracer_is_a_noop(self) -> None:
        tracer = NullTracer()

        with tracer.run(run_id="run_1", session_id="session_1", message="hi") as run:
            self.assertIsNone(run.trace_id)
            self.assertIsNone(run.url())
            with tracer.generation(name="g", model="m") as generation:
                generation.finish(output="ok", usage={"input": 1})
            with tracer.tool(name="t") as tool:
                tool.finish(status="ok")

        tracer.flush()
        tracer.shutdown()

    # Regression scenario: an enabled switch without credentials stays offline.
    def test_configure_disables_tracer_without_credentials(self) -> None:
        settings = LangfuseSettings(
            enabled=True,
            host="",
            public_key="",
            secret_key="",
            sample_rate=1.0,
            environment="test",
        )

        tracer = configure_agent_tracer(settings)

        self.assertIsInstance(tracer, NullTracer)
        self.assertIs(get_agent_tracer(), tracer)

    # Regression scenario: a construction failure cannot break startup.
    def test_configure_falls_back_when_client_construction_fails(self) -> None:
        with patch.object(
            agent_tracing, "LangfuseTracer", side_effect=RuntimeError("no sdk")
        ):
            tracer = configure_agent_tracer(ACTIVE_SETTINGS)

        self.assertIsInstance(tracer, NullTracer)

    # Regression scenario: a run reuses its durable id as the trace id.
    def test_run_uses_deterministic_trace_id_and_session(self) -> None:
        client = _FakeClient()
        propagate = _FakePropagate()
        tracer = LangfuseTracer(ACTIVE_SETTINGS, client=client, propagate=propagate)

        with tracer.run(
            run_id="run_abc",
            session_id="session_1",
            owner_id="owner_1",
            message="今天涨停家数",
            metadata={"model": "deepseek-v4-flash"},
        ) as run:
            self.assertEqual(run.trace_id, "trace-run_abc")
            self.assertEqual(run.url(), "https://example.test/trace/trace-run_abc")

        observation = client.observation_calls[0]
        self.assertEqual(observation["as_type"], "agent")
        self.assertEqual(observation["trace_context"], {"trace_id": "trace-run_abc"})
        self.assertEqual(observation["metadata"]["model"], "deepseek-v4-flash")
        self.assertEqual(propagate.calls[0]["session_id"], "session_1")
        self.assertEqual(propagate.calls[0]["user_id"], "owner_1")

    # Regression scenario: generations keep exact usage and survive failures.
    def test_generation_records_usage_and_marks_errors(self) -> None:
        client = _FakeClient()
        tracer = LangfuseTracer(ACTIVE_SETTINGS, client=client, propagate=_FakePropagate())

        with tracer.generation(
            name="react_decision",
            model="deepseek-v4-flash",
            input=[{"role": "user", "content": "hi"}],
            parameters={"temperature": 0.2},
        ) as generation:
            generation.finish(
                output={"tool_calls": []},
                usage={"input": 10, "output": 5, "total": 15},
            )

        update = client.spans[0].updates[0]
        self.assertEqual(
            update["usage_details"], {"input": 10, "output": 5, "total": 15}
        )
        self.assertNotIn("level", update)

        with tracer.generation(name="react_decision", model="m") as generation:
            generation.finish(error=ValueError("boom"))

        error_update = client.spans[1].updates[0]
        self.assertEqual(error_update["level"], "ERROR")
        self.assertIn("ValueError", error_update["status_message"])

    # Regression scenario: an exception in the block is recorded and re-raised.
    def test_generation_marks_and_reraises_body_errors(self) -> None:
        client = _FakeClient()
        tracer = LangfuseTracer(ACTIVE_SETTINGS, client=client, propagate=_FakePropagate())

        with self.assertRaises(ValueError):
            with tracer.generation(name="g", model="m"):
                raise ValueError("stream dropped")

        self.assertEqual(client.spans[0].updates[0]["level"], "ERROR")

    # Regression scenario: empty results and failures stay distinguishable.
    def test_tool_span_marks_empty_and_error_status(self) -> None:
        client = _FakeClient()
        tracer = LangfuseTracer(ACTIVE_SETTINGS, client=client, propagate=_FakePropagate())

        with tracer.tool(
            name="limit_up_events", arguments={"trade_date": "2026-10-23"}
        ) as tool:
            tool.finish(status="empty", output={"rows": []})

        first = client.spans[0].updates[0]
        self.assertEqual(first["metadata"], {"status": "empty"})
        self.assertEqual(first["level"], "DEFAULT")

        with tracer.tool(name="stock_kline") as tool:
            tool.finish(status="error", error=ValueError("bad symbol"))

        second = client.spans[1].updates[0]
        self.assertEqual(second["level"], "ERROR")
        self.assertIn("bad symbol", second["status_message"])

    # Regression scenario: an exporter failure never skips the model call.
    def test_observation_failure_keeps_the_call_site_running(self) -> None:
        client = _FakeClient()
        client.fail_observation = True
        tracer = LangfuseTracer(ACTIVE_SETTINGS, client=client, propagate=_FakePropagate())

        ran = False
        with tracer.generation(name="g", model="m") as generation:
            ran = True
            generation.finish(output="ok")

        self.assertTrue(ran)

    # Regression scenario: shutdown flushes once and restores the no-op tracer.
    def test_shutdown_resets_the_process_tracer(self) -> None:
        client = _FakeClient()
        agent_tracing._tracer = LangfuseTracer(
            ACTIVE_SETTINGS, client=client, propagate=_FakePropagate()
        )

        shutdown_agent_tracer()

        self.assertIsInstance(get_agent_tracer(), NullTracer)
        self.assertTrue(client.shutdown_called)

    # Regression scenario: credential-shaped keys and values never reach export.
    def test_feedback_score_mirrors_vote_and_fails_open(self) -> None:
        client = _FakeClient()
        tracer = LangfuseTracer(
            ACTIVE_SETTINGS, client=client, propagate=_FakePropagate(),
        )
        self.assertTrue(tracer.feedback_score(
            run_id="run-1", vote="down",
            reasons=["data_error"], comment="日期不对",
        ))
        self.assertEqual(client.scores[0]["trace_id"], "trace-run-1")
        self.assertEqual(client.scores[0]["value"], 0.0)
        self.assertIn("data_error", client.scores[0]["comment"])
        client.create_score = lambda **_: (_ for _ in ()).throw(RuntimeError("offline"))
        self.assertFalse(tracer.feedback_score(run_id="run-1", vote="up"))
        self.assertFalse(NullTracer().feedback_score(run_id="run-1", vote="up"))

    def test_mask_trace_data_redacts_credentials(self) -> None:
        masked = mask_trace_data(data={
            "api_key": "sk-live-1234567890",
            "headers": {"Authorization": "Bearer abcdefghijklmnop"},
            "note": "key sk-live-1234567890 leaked",
            "rows": [{"symbol": "000001", "content": "普通文本"}],
        })

        self.assertEqual(masked["api_key"], "[redacted]")
        self.assertEqual(masked["headers"]["Authorization"], "[redacted]")
        self.assertEqual(masked["note"], "key [redacted] leaked")
        self.assertEqual(masked["rows"][0]["symbol"], "000001")
        self.assertEqual(masked["rows"][0]["content"], "普通文本")

    # Regression scenario: oversized outputs are bounded before export.
    def test_generation_output_is_bounded(self) -> None:
        client = _FakeClient()
        tracer = LangfuseTracer(ACTIVE_SETTINGS, client=client, propagate=_FakePropagate())

        with tracer.generation(name="g", model="m") as generation:
            generation.finish(output={"rows": ["x" * 1000] * 100})

        output = client.spans[0].updates[0]["output"]
        self.assertIsInstance(output, str)
        self.assertIn("truncated", output)

    # Regression scenario: partial usage is never reported as measured.
    def test_usage_details_require_every_count(self) -> None:
        self.assertIsNone(usage_details(None, 1, 2))
        self.assertIsNone(usage_details(1, None, 2))
        self.assertEqual(usage_details(1, 2, 3), {"input": 1, "output": 2, "total": 3})


if __name__ == "__main__":
    unittest.main()

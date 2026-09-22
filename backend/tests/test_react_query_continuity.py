"""Synthetic protocol regressions, not real-market evaluation cases."""
import json
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import ValidationError

from app.agents.react_runtime import runtime
from app.agents.react_runtime.compliance import ComplianceReview
from app.agents.react_runtime.context import prepare_query_reference
from app.agents.tools import TOOL_SCHEMAS, ToolResult
from app.models import AgentChatRequest, ChatSessionMessage
from app.services.prompt_security import review_input


def reply(name, args):
    return AIMessage(content="", tool_calls=[{'name': name, 'args': args, 'id': name}])


def previous(session='same', *, status='success', date='2026-09-21'):
    return ChatSessionMessage(message_id='old', session_id=session, role='assistant',
        content='private old answer', status=status, created_at='2026-09-22T00:00:00Z',
        metadata={'tool_results': [{'name': 'limit_up_events', 'status': 'success',
            'input': {'trade_date': date, 'market': 'main_board', 'board_height': 1, 'limit': 100},
            'output': {'secret_old_market_value': 123456, 'evidence_id': 'old-evidence-id'}}]})


def test_query_reference_contains_inputs_only_and_never_old_facts():
    request = AgentChatRequest(session_id='same', message='技术测试追问')
    messages = prepare_query_reference(request, [previous()], {'limit_up_events'})
    assert len(messages) == 1
    text = messages[0].content
    assert 'main_board' in text and '2026-09-21' in text
    assert all(value not in text for value in ['private old answer', '123456', 'old-evidence-id', 'secret_old_market_value'])


@pytest.mark.parametrize('history,tools', [
    ([previous('other')], {'limit_up_events'}),
    ([previous(status='error')], {'limit_up_events'}),
    ([previous()], set()),
    ([], {'limit_up_events'}),
])
def test_no_foreign_failed_disabled_or_missing_query_reference(history, tools):
    assert prepare_query_reference(AgentChatRequest(session_id='same', message='追问'), history, tools) == []


def test_query_reference_uses_latest_answer_only_and_does_not_silently_truncate():
    request = AgentChatRequest(session_id='same', message='追问')
    newer = previous(date='2026-09-18')
    text = prepare_query_reference(request, [previous(), newer], {'limit_up_events'})[0].content
    assert '2026-09-18' in text and '2026-09-21' not in text
    newer.metadata = {}
    assert prepare_query_reference(request, [previous(), newer], {'limit_up_events'}) == []
    newer.metadata = previous().metadata
    newer.metadata['tool_results'][0]['input']['query'] = 'x' * 13000
    assert prepare_query_reference(request, [newer], {'limit_up_events'}) == []


@pytest.mark.parametrize('message,scope', [
    ('改查9月18日，其他条件不变。', 'follow_up'),
    ('换到上周五看看，同一范围。', 'follow_up'),
    ('它最近5个交易日涨了多少？', 'follow_up'),
    ('不继续上面的任务了，查今天龙虎榜。', 'standalone'),
])
def test_semantic_scope_is_used_without_keyword_override(message, scope):
    class Model:
        def generate_messages(self, messages, tools, **kwargs):
            assert 'context_mode' in tools[0]['function']['parameters']['required']
            return reply('submit_input_security_review', {'decision': 'allow', 'signals': [],
                'reason': 'scripted semantic decision, not model accuracy evidence',
                'request_kind': 'research', 'context_mode': scope})
    from datetime import date
    result = review_input(Model(), message=message, timeout_seconds=10, anchor_date=date(2026, 9, 22))
    assert result.context_mode == scope


def test_missing_semantic_scope_fails_closed():
    class Model:
        def generate_messages(self, *args, **kwargs):
            return reply('submit_input_security_review', {'decision': 'allow', 'signals': [],
                'reason': 'incomplete review', 'request_kind': 'research'})
    with pytest.raises(ValidationError):
        review_input(Model(), message='追问', timeout_seconds=10)


@pytest.mark.parametrize('scope', ['follow_up', 'standalone'])
def test_runtime_routes_query_reference_and_refreshes_changed_date(monkeypatch, scope):
    monkeypatch.setattr(runtime, 'review_answer', lambda *a, **k: ComplianceReview(
        decision='allow', violations=[], reason='synthetic protocol test'))
    calls = []
    def query(**args):
        calls.append(args)
        assert str(args['trade_date']) == '2026-09-18'
        assert args['market'] == 'main_board' and args['board_height'] == 1
        return ToolResult(name='limit_up_events', input=args, output={'trade_date': '2026-09-18',
            'matched_count': 0, 'returned_count': 0, 'events': []}, summary='synthetic empty')
    registry = SimpleNamespace(events=[], profile='test', schemas=lambda: [s for s in TOOL_SCHEMAS if s.name == 'limit_up_events'],
        is_enabled=lambda n: n == 'limit_up_events', limit_up_events=query)
    class Model:
        def generate_messages(self, messages, tools, **kwargs):
            if tools[0]['function']['name'] == 'submit_input_security_review':
                return reply('submit_input_security_review', {'decision': 'allow', 'signals': [],
                    'reason': 'scripted review', 'request_kind': 'research', 'context_mode': scope})
            observations = [json.loads(m.content) for m in messages if isinstance(m, ToolMessage)]
            if not observations:
                text = '\n'.join(m.content for m in messages[1:])
                assert ('上一轮实际查询参数' in text) == (scope == 'follow_up')
                assert 'private old answer' not in text and 'old-evidence-id' not in text
                if scope == 'standalone':
                    return reply('finish', {'status': 'clarify', 'answer': '请明确筛选条件。'})
                assert 'main_board' in text and '2026-09-21' in text
                return reply('limit_up_events', {'trade_date': '2026-09-18', 'market': 'main_board', 'board_height': 1, 'limit': 100})
            return reply('finish', {'status': 'empty', 'answer': '技术测试日期无匹配记录。',
                'evidence_ids': [observations[-1]['evidence_id']]})
    response = runtime.run(AgentChatRequest(session_id='same', message='改查9月18日，其他条件不变。'),
        registry, Model(), history=[previous()])
    assert response.task_status == ('empty' if scope == 'follow_up' else 'clarify')
    assert len(calls) == (1 if scope == 'follow_up' else 0)
    execution = next(t.output for t in response.tool_results if t.name == 'react_execution')
    assert 'old-evidence-id' not in execution['evidence']

# Reading the LimitUpLab code

This guide is a starting point for following the English comments in the code.
The project is a research workbench. Scores, filters and historical statistics
come from deterministic code and recorded data; the model plans and explains.

## Start with one user question

1. `frontend/src/components/AgentChatDock.tsx`, `sendMessage`: builds the request
   with the question, session ID and current stock/date context. Its callback
   displays progress and draft answer fragments.
2. `frontend/src/api.ts`, `streamAgentChatMessage`: sends the POST and parses
   server-sent events (SSE). A network chunk can contain part of a record or
   several records; the buffer handles both cases.
3. `backend/app/routers/agents.py`, `stream_first_board_agent_chat`: verifies
   conversation ownership, reserves the request quota, saves the user's message
   and starts the worker. It also owns persistence and usage accounting.
4. `backend/app/services/session_memory.py`, `prepare_session_context`: selects
   bounded recent messages and optionally updates an older-message summary.
   Memory resolves conversational references; it is not current market evidence.
5. `backend/app/agents/chat.py`, `answer_first_board_chat`: coordinates the answer
   paths. It handles security and scope boundaries, tries model planning and
   falls back to deterministic domain handlers when planning is unavailable.
6. `_generate_llm_query_plan`: asks the model for capabilities, intent and context
   mode. `capability_contract.py` maps capabilities to evidence tools. Query
   contracts and normalization helpers derive and constrain business arguments.
7. `backend/app/agents/tool_execution/__init__.py`, `execute_tool_calls`: checks
   tool availability and dispatches in order to domain handlers. Those handlers
   call `AgentToolRegistry` in `tools.py`, which connects services and repositories.
8. `backend/app/agents/tool_policy.py`, `reconcile`: checks the executed results
   and adds required evidence omitted by the plan. Repair reasons stay in Trace.
9. `_answer_with_llm_tool_agent` in `chat.py`: composes tool facts and selects a
   deterministic template or a facts-only model answer. Specific completeness
   and safety checks can replace model prose with the prepared template.
10. `AgentChatResponse` in `models.py`: derives stock mentions, evidence cards,
    suggested questions and planner-versus-final audit metadata. The route saves
    the answer and sends `completed`; the frontend replaces any provisional text.

The non-streaming `/chat` route shares the answer function. Capability questions,
missing-data responses, structured lists and failures can take shorter paths.
There is no requirement that every turn call every Agent role or call a model
exactly twice. Memory refreshes and fallback attempts can add calls; deterministic
paths can omit them.

## Follow the data behind an answer

| Code area | Responsibility | What to look for in the comments |
| --- | --- | --- |
| `collectors/` | Adapt remote providers to local typed records | Source fields, units, timezones, timeouts, unavailable data |
| `repositories/` | Read/write SQLite records | Keys, transactions, ownership, immutable snapshots |
| `services/` | Calculate and combine domain facts | Trading-day windows, denominators, eligibility, missing values |
| `agents/first_board.py` | Filter candidates, build Facts and score | Exclusions versus missing data, policy version, confidence |
| `agents/query_contract.py` | Interpret local event queries | Date, market, board height, event status, ordering and limits |
| `post_limit_query_contract.py` | Interpret post-limit shape research | Anchor, cutoff, shape thresholds and statistical windows |
| `agents/tools.py` | Expose domain operations to chat | Tool inputs, full output, compact trace, active profile |
| `models.py` | Define API and stored data shapes | Optional fields, validators and uniform result states |
| `backend/scripts/` | Run collection, backfill and evaluation workflows | Step order, restart behavior, reports and failure states |
| `frontend/src/` | Fetch, format and render facts | State updates, stale requests, empty states and source links |
| `backend/tests/`, `frontend/tests/` | Explain expected behavior with controlled cases | Regression scenario, fixtures and assertions |
| `deploy/`, `scripts/` | Start, verify and deploy the system | Process ownership, locks, backups and recovery |

## Terms used in function comments

- **Facts**: structured evidence from tools or deterministic calculations, used
  by the answer writer. Facts are distinct from the conversation summary.
- **Trace**: the inspectable record of tool inputs, outcomes, compact output and
  repair reasons. It helps explain how an answer was produced.
- **Query contract**: the backend's normalized interpretation of a research
  question. It keeps filtering, tool execution and answer metadata consistent.
- **Profile**: the configured set of capabilities/tools allowed for this Agent.
- **Fallback**: an explicit alternate path used when the preferred path is
  unavailable or fails validation. Read its output state rather than assuming
  the fallback has the same evidence coverage.
- **Anchor date**: the reference event's date, such as a stock's limit-up day.
- **Data cutoff / data_as_of**: the latest data boundary allowed for a query or
  recorded prediction. It is different from the report's generation time.
- **Outcome**: what was observed after a recorded signal/prediction. Missing
  future bars do not count as zero returns or failed promotions.
- **Live snapshot**: a recorded published cohort with provenance. A recalculated
  historical observation does not become live merely because it has a score.
- **Cohort / denominator**: the exact set of eligible observations behind a rate.
  Check maturity, missing data and deduplication before interpreting a statistic.
- **Test double / fixture**: controlled data or a replacement dependency used by
  a test. A passing fixture test is not a claim about a live provider's accuracy.

## Reading a function

Read its purpose comment/docstring first, then its inputs and return type. Follow
early-return conditions before the main calculation: these usually explain
unsupported input, missing history, cached results or safety boundaries. In long
functions, the step comments describe why operations occur in that order. Read
the returned model and the calling function together to see how errors and None
values become a visible UI state.

Existing runtime docstrings are retained. New Python comments do not change
`__doc__`, generated API descriptions, prompt text or SQL. Frontend callback
comments are code comments, including inside JSX expressions, and are not UI copy.

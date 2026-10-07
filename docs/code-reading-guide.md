# Reading the LimitUpLab code

This guide is a starting point for following the English comments in the code.
The project is a research workbench. Scores, filters and historical statistics
come from deterministic code and recorded data; the model plans and explains.

## Start with one user question

1. `frontend/src/components/AgentChatDock.tsx`: sends the question and session
   context, displays a provisional live answer and progress, then replaces the
   draft with the persisted `completed` response.
2. `frontend/src/utils/agentChatTransport.ts`: parses SSE, reconnects once with
   the event cursor, and preserves request identity for an in-page retry.
   `agentAnswerBuffer.ts` handles revisions, resets, Unicode offsets and
   duplicate chunks without losing the draft during a read-only reconnect.
3. `backend/app/routers/react_chat.py`: owns authenticated request admission,
   workers, cancellation, reconnect and atomic completion persistence.
4. `backend/app/agents/react_runtime/lifecycle.py`: stores run identity,
   checkpoints, call results and events, with owner isolation and idempotency.
5. `backend/app/agents/chat.py`: the small public ReAct entry. There is no
   separate capability Planner, legacy routing or template-mode switch.
6. `backend/app/agents/react_runtime/runtime.py`: the single compiled StateGraph
   performs model decisions, Policy checks, tool calls, observation and final
   validation. Model/tool/deadline limits bound the same loop.
7. `react_runtime/catalog.py` derives strict Pydantic arguments and LangChain
   tools from the public contracts in `agents/tools.py`; `react_runtime/tools.py`
   validates and dispatches calls. The old `tool_schema.py` has been removed.
8. `react_runtime/contracts.py` and `evidence.py`: define control tools,
   controlled computation, full result references and provenance.
   `task_contract.py` and `display_fields.py` preserve the interpreted task,
   pending slots, explicit output fields and unresolved display requirements.
9. `react_runtime/context.py`: provides no history by default. For a classified
   follow-up, it extracts entity names/codes and the last answer's actual tool
   arguments, without replaying old tasks, prose or evidence. `runtime.py` passes
   memory constraints by default and adds research context only for follow-ups.
   `tools.py` fills an omitted date from the input review's `requested_date`,
   then checks tool date support and the actual response date.
10. `services/langchain_provider.py` and `react_runtime/answer_stream.py`:
    receive native tool-call chunks and decode only `finish.answer` for display.
    `answer_delivery.py` manages provisional revisions; `rendering.py` fills
    the declared evidence table before the final compliance review.
11. `models.py`: builds user-visible metadata and retains compatibility for
    historical stored traces. `react_chat.py` persists the final response once.

## Follow the data behind an answer

| Code area | Responsibility | What to look for in the comments |
| --- | --- | --- |
| `collectors/` | Adapt remote providers to local typed records | Source fields, units, timezones, timeouts, unavailable data |
| `repositories/` | Read/write SQLite records | Keys, transactions, ownership, immutable snapshots |
| `services/` | Calculate and combine domain facts | Trading-day windows, denominators, eligibility, missing values |
| `agents/first_board.py` | Filter candidates, build Facts and score | Exclusions versus missing data, policy version, confidence |
| `agents/query_contract.py` | Shared argument types and event normalization | The natural-language query compiler has been retired |
| `post_limit_query_contract.py` | Interpret post-limit shape research | Anchor, cutoff, shape thresholds and statistical windows |
| `agents/tools.py` | Expose domain operations to chat | Tool inputs, full output, compact trace, active profile |
| `models.py` | Define API and stored data shapes | Optional fields, validators and uniform result states |
| `backend/scripts/` | Run collection and backfill workflows | Step order, restart behavior, reports and failure states |
| `frontend/src/` | Fetch, format and render facts | State updates, stale requests, empty states and source links |
| `backend/tests/`, `frontend/tests/` | Explain expected behavior with controlled cases | Regression scenario, fixtures and assertions |
| `backend/evals/golden/` | Run the production Agent against a frozen synthetic world | Independent expected answers, budgets, provenance, judge references and review states |
| `deploy/`, `scripts/` | Start, verify and deploy the system | Process ownership, locks, backups and recovery |

## Terms used in function comments

- **Facts**: structured evidence from tools or deterministic calculations, used
  by the answer writer. Facts are distinct from the conversation summary.
- **Trace**: the inspectable record of native model decisions, Policy allow/reject,
  tool inputs/outcomes, evidence references and the final answer check.
- **Provisional answer**: live text that may be withdrawn by `answer_reset`.
  Only `completed` is the persisted final response; a completed reconnect skips
  draft events entirely.
- **Evidence table**: a single table rendered from stored current-run rows via
  `finish.table`. Preview omission and source truncation are distinct; returned
  rows are not automatically the complete market universe.
- **Query contract**: a deterministic domain parameter contract used inside
  specific tools. Production chat semantics come
  from the ReAct message/tool loop rather than one global regex parser.
- **Profile**: the configured set of capabilities/tools allowed for this Agent.
- **Fallback**: an explicit source- or role-specific alternate path. The retired
  general chat template and Requests chat-runtime rollback are not current
  production fallbacks; provider failure must remain visible as error/partial.
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

The former chat evaluation framework and Claim Ledger are retired. The current
[Golden suite](Agent_Golden_Evaluation.md) runs separately under
`backend/evals/golden/`; distinguish full Agent runs from judge-only diagnostics.
Follow the runtime tests and `badCase.md` for regression history, and the
[V1.5 milestone](V1.5_Milestone.md) for verified results and remaining limits.
The separate prediction Evaluation Agent still classifies recorded market outcomes.

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

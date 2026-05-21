# LimitUpLab Agent Development Plan

This document captures the planned agent direction for LimitUpLab. The product
is positioned as an after-close A-share limit-up review and research lab, not a
live trading or investment-advice system.

## Product Principle

LimitUpLab should not add an AI chat box just because AI is popular. Agent
features should help users review, compare, and verify market behavior with
data-backed reasoning.

The core rule:

```text
Facts first, narrative second.
```

The system should compute reliable facts and statistics first. LLMs can then
explain, summarize, and organize those facts, but they should not invent market
data or make unsupported claims.

## What Agent Features Can Do

### Daily Review Agent

Generate an after-close review for the latest trading day:

- Market sentiment summary
- Limit-up count, first-board count, continued-board count
- Failed limit-up count and failed rate
- Highest board height and board ladder
- Hot industries and concepts
- Risk signals such as high failed rate or weak seal quality
- Review watch points for the next session

This should be framed as market review, not trading advice.

### Stock Review Agent

Explain a stock from its detail page:

- Current board height and market position
- First and last limit-up time
- Seal count and break count
- Closed limit or failed/resealed status
- Five-day daily K-line context
- Latest trading-day intraday review K-line context
- Turnover amount, turnover rate, industry, and concept
- Tags such as high-board stock, late seal, high volatility, failed-reseal

This should explain why a stock is worth reviewing, not whether to buy it.

### Similar Market Agent

Find historical trading days similar to the latest market structure.

This is the most agent-like first milestone because it requires multi-step
reasoning:

1. Build a feature vector for the latest trading day.
2. Build comparable feature vectors for historical trading days.
3. Calculate similarity.
4. Return the most similar historical samples.
5. Explain why each sample is similar.
6. Show data limits and sample size.

Example factors:

- Limit-up count
- First-board count
- Continued-board count
- Failed limit-up rate
- Highest board height
- Total limit-up turnover amount
- Hot concept concentration
- Board ladder shape

### Research Query Agent

Later, users should be able to ask data-backed questions such as:

- In the last 60 trading days, how did first-board stocks sealed before 10:00 perform?
- What happens after days with high failed rate but high board height?
- How often do failed-reseal stocks continue the next day?
- Which concepts showed the strongest continuation after their first breakout day?

This requires query planning, statistical tools, result validation, and clear
limitations.

## AI Application Development Challenges

### Data Reliability

The hardest problem is not prompting. It is trustworthy data.

Risks:

- AKShare source fields may change.
- Data providers may be temporarily unavailable.
- Different providers may use different market data definitions.
- Some stocks may have missing K-line data.
- Special cases such as ST stocks, new listings, suspended stocks, and different board rules need handling.

Needed safeguards:

- Data source health checks
- Field validation
- Data cutoff timestamps
- Missing-data handling
- Fallback sources where possible
- Clear data-source notes in UI and reports

### Verifiability

Agent output must be traceable to facts.

Bad:

```text
Market sentiment was weak today.
```

Better:

```text
Market sentiment was weak today because the failed limit-up rate was 56.25%,
above the configured high-risk threshold, while the highest board remained at
8 boards.
```

The output should be split into:

- `facts`: structured, machine-verifiable data
- `narrative`: human-readable explanation

### Context Management

Do not send all raw data to an LLM.

Use targeted context:

- Daily review needs market summary, board ladder, hot concepts, and risk stats.
- Stock review needs one stock event, recent daily K-line, and latest trading-day intraday review data.
- Similar market search needs compact feature vectors, not full event lists.

### Guardrails

The project touches financial markets, so outputs must avoid:

- Buy/sell recommendations
- Target prices
- Promises of return
- Claims such as "will rise tomorrow"
- Personalized investment advice

Allowed framing:

- Historical review
- Risk observation
- Data-backed comparison
- Research hypothesis
- Watch points for future review

### Evaluation

AI output needs tests beyond normal API tests.

Possible eval checks:

- Does the review mention numbers not present in facts?
- Does it include investment advice?
- Does it correctly cite limit-up count, failed rate, and highest board?
- Is sentiment consistent with the configured rules?
- Does it state limitations when sample size is small?

### Cost and Latency

Do not call an LLM on every page load.

Cache likely outputs:

- Daily review by `trade_date`
- Stock review by `symbol + trade_date`
- Similar market results by `trade_date + algorithm_version`

Use rule-based output as fallback when LLM calls fail.

## Recommended Architecture

```text
backend/app/
  agents/
    __init__.py
    facts.py
    daily_review.py
    stock_review.py
    similar_market.py
    prompts.py
    llm.py
    guardrails.py
  routers/
    agents.py
```

### Facts Layer

Responsible for deterministic data extraction.

Examples:

- latest market summary
- board ladder
- failed-rate stats
- hot concept concentration
- stock event facts
- daily K-line facts
- trading-day intraday review facts

### Tools / Statistics Layer

Responsible for reusable calculations:

- board ladder
- seal-time distribution
- failed-rate buckets
- concept concentration
- market feature vector
- similarity score
- stock tags

### Narrative Layer

Responsible for turning facts into Markdown.

First implementation should be rule-based. LLM integration comes later.

### LLM Layer

Responsible for calling an LLM with strict inputs and outputs.

The LLM should receive structured facts, not raw database access.

### Guardrail Layer

Responsible for checking:

- no investment advice
- no unsupported numbers
- no future-return promises
- required facts are included

## Phase Plan

### Phase 1: Rule-Based Agent Foundation

Goal: useful output without LLM dependency.

Implement:

- `POST /api/agents/daily-review`
- `POST /api/agents/stock-review`
- deterministic facts builders
- rule-based Markdown generation
- frontend buttons and review panels

Benefits:

- Fast
- Free to run
- Easy to test
- Sets up the facts contract before LLM integration

### Phase 2: Similar Market Agent

Goal: make the product feel like a research lab.

Implement:

- daily market feature vector
- similarity calculation
- `GET /api/agents/similar-markets?trade_date=latest`
- top similar historical dates
- similarity explanation
- frontend "Similar Markets" section

Example response:

```json
{
  "trade_date": "2026-05-20",
  "matches": [
    {
      "trade_date": "2026-04-18",
      "score": 0.87,
      "reasons": [
        "failed limit-up rate was close",
        "highest board height was similar",
        "continued-board count was similar"
      ]
    }
  ]
}
```

### Phase 3: LLM Narrative Upgrade

Goal: improve readability while keeping facts deterministic.

Add:

- `agents/llm.py`
- prompt templates
- optional LLM generation for daily review and stock review
- fallback to rule-based output
- cached review storage

Prompt principles:

```text
You are an A-share after-close market review assistant.
Use only the provided facts.
Do not give investment advice.
Do not predict returns.
If data is missing, state that it is missing.
Output concise Markdown.
```

### Phase 4: Research Query Agent

Goal: answer user research questions with multi-step planning.

Needed capabilities:

- parse user question
- choose relevant tools
- run statistics
- summarize results
- show sample size and limitations
- reject unsupported or advice-seeking questions

Example:

```text
统计最近 60 个交易日，10:00 前封板的首板票，次日继续涨停比例。
```

### Phase 5: User Review Memory

Goal: support user-specific review workflow.

Possible features:

- saved review templates
- preferred board ranges
- ignored concepts
- personal notes
- daily review history

This should be explicit and editable by the user.

## Suggested First Implementation

Start with the Similar Market Agent.

Why:

- It is more agent-like than simple summarization.
- It uses existing persisted daily event data.
- It makes LimitUpLab feel like a research tool.
- It can be implemented without LLM first.

Steps:

1. Create a market feature vector function.
2. Add tests for feature extraction.
3. Add similarity score calculation.
4. Add tests for similarity ranking.
5. Add an API endpoint.
6. Add a frontend panel.
7. Later, add LLM explanation using the deterministic matches.

## Non-Goals

LimitUpLab agents should not:

- provide buy/sell advice
- generate target prices
- promise returns
- trade automatically
- monitor live markets as a real-time alert system
- hide unsupported assumptions in fluent text

## Long-Term Direction

The best version of LimitUpLab is not a dashboard with a chatbot attached.

It should become a data-backed review workspace where agents:

- find historical analogs
- summarize market structure
- explain stock behavior
- test research hypotheses
- help users write cleaner review notes
- keep every conclusion tied to verifiable data

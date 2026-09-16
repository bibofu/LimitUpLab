"""Finite, auditable scenario expansion; never wildcard-match unknown queries."""

from copy import deepcopy
from itertools import product

from app.agents.react_runtime.catalog import arguments_model
from app.agents.tools import TOOL_SCHEMAS


MODELS = {s.name: arguments_model(s) for s in TOOL_SCHEMAS}
LIST_FIELDS = {"post_limit_screen": "candidates", "sector_stock_ranking": "items",
               "dragon_tiger_list": "items", "finance_news": "items", "stock_news": "items",
               "remote_limit_up_pool": "items", "web_search": "results", "rating_evaluation": "evaluations"}


def record_of(item):
    return deepcopy({key: item[key] for key in ("tool", "arguments", "payload", "state", "checks")})


def expand_scenario(item, records, catalog):
    """All variants derive from declared source facts; unrelated args stay exact."""
    config = item.get("scenario")
    if not config:
        return records
    sources = deepcopy(records)
    for case_id in config.get("supporting_cases", []):
        sources.append(record_of(catalog[case_id]))
    sources.extend(deepcopy(config.get("supporting_records", [])))
    if config.get("all_other_sources_unavailable"):
        for source in sources:
            if source["state"] == "error":
                symbol = source["arguments"].get("symbol")
                source["match_policy"] = ({"kind": "source_error", "bindings": {"symbol": symbol}}
                    if symbol else {"kind": "source_error", "global_source": True})
        seeds = {c["tool"]: c["arguments"] for c in catalog.values()}
        seeds.update(market_summary={}, limit_up_events={}, market_event_pool={"event_type": "limit_up"})
        existing = {r["tool"] for r in sources}
        for name, args in seeds.items():
            if name not in existing:
                sources.append({"tool": name, "arguments": deepcopy(args), "payload": {
                    "error": "synthetic scenario source outage", "source_errors": [name + " unavailable"]},
                    "state": "error", "checks": [], "match_policy": {"kind": "source_error", "global_source": True}})
    expanded = {}
    for source in sources:
        tool, arguments = source["tool"], source["arguments"]
        variants = [source]
        # This scenario's ratings universe is explicitly complete, not a Top-N preview.
        if config.get("complete_rating_universe") and tool == "first_board_ratings":
            variants = []
            candidates = source["payload"]["candidates"]
            flat = source["payload"]["top_candidates"]
            for symbols in (None, ["600001"]):
                clone = deepcopy(source)
                clone["arguments"].pop("symbols", None)
                if symbols:
                    clone["arguments"]["symbols"] = symbols
                clone["payload"]["candidates"] = [r for r in candidates if symbols is None or r["facts"]["symbol"] in symbols]
                clone["payload"]["top_candidates"] = [r for r in flat if symbols is None or r["symbol"] in symbols]
                variants.append(clone)
            for query in config.get("rating_queries", []):
                rows = [r for r in flat if any(query.casefold() in str(r.get(k, "")).casefold()
                                              for k in ("symbol", "name", "industry", "concept"))]
                variants.append({"tool": "first_board_filter", "arguments": {"trade_date": arguments["trade_date"], "query": query},
                    "payload": {"trade_date": arguments["trade_date"], "items": rows, "matched_count": len(rows), "source": "first-board-ratings"},
                    "state": "ok" if rows else "empty", "checks": []})
        for variant in variants:
            name = variant["tool"]
            model = MODELS[name]
            options = {}
            if config.get("all_other_sources_unavailable") and name == "stock_activity":
                spec = model.model_json_schema()["properties"]["days"]
                options["days"] = range(spec["minimum"], spec["maximum"] + 1)
            if config.get("limit_closure") and not variant.get("match_policy"):
                for key in ("limit", "news_limit"):
                    spec = model.model_json_schema()["properties"].get(key)
                    if spec:
                        low, high = spec["minimum"], spec["maximum"]
                        if not 1 <= low <= high <= 100:
                            raise ValueError("unreviewed limit range")
                        options[key] = range(low, high + 1)
            if name == "web_search" and config.get("search_queries"):
                options["query"] = list(dict.fromkeys([variant["arguments"]["query"], *config["search_queries"]]))
            if name == "finance_news" and config.get("news_queries"):
                options["query"] = config["news_queries"]
            for values in product(*options.values()):
                clone = deepcopy(variant)
                clone["arguments"].update(dict(zip(options, values)))
                payload = clone["payload"]
                if "limit" in options and name in LIST_FIELDS and clone["state"] != "error":
                    key = LIST_FIELDS[name]
                    rows = payload[key]
                    limit = clone["arguments"]["limit"]
                    payload[key] = rows[:limit]
                    if "requested_limit" in payload:
                        payload["requested_limit"] = limit
                    if "returned_count" in payload:
                        payload["returned_count"] = len(payload[key])
                    if name == "remote_limit_up_pool":
                        # Production matched_count is the returned filtered rows here.
                        payload["matched_count"] = len(payload[key])
                if name == "web_search" and clone["state"] != "error":
                    payload["query"] = clone["arguments"]["query"]
                    if config.get("search_entity"):
                        clone["match_policy"] = {"kind": "search_terms", "entity": config["search_entity"],
                            "terms": ["公告", "最新", "官方", "原文", "股票"]}
                canonical = model.model_validate(clone["arguments"]).model_dump(mode="json", exclude_none=True)
                signature = (name, repr(sorted(canonical.items())))
                previous = expanded.get(signature)
                if previous and (previous["payload"] != clone["payload"] or previous["state"] != clone["state"]):
                    raise ValueError("contradictory facts for scenario route")
                expanded[signature] = clone
    return list(expanded.values())

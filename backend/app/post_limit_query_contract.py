"""Canonical natural-language contract for post-limit research questions."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Literal

POST_LIMIT_QUERY_VERSION = "post-limit-query-v1"
PostLimitMode = Literal["screen", "path", "statistics"]
PostLimitShape = Literal[
    "high_drawdown",
    "volume_consolidation",
    "pullback_stabilizing",
    "strong_nonconsecutive",
    "broken_board_repair",
    "second_to_third",
]

SHAPE_ALIASES: tuple[tuple[PostLimitShape, tuple[str, ...]], ...] = (
    ("volume_consolidation", ("横盘缩量", "缩量横盘", "缩量整理", "涨停后整理")),
    ("pullback_stabilizing", ("回撤企稳", "回调企稳", "止跌企稳", "企稳修复")),
    ("strong_nonconsecutive", ("强势不连板", "断板强势", "强势断板")),
    ("broken_board_repair", ("断板修复", "断板反包", "断板后修复")),
    ("second_to_third", ("2进3", "二进三", "2 进 3", "二板进三板")),
    (
        "high_drawdown",
        (
            "高位大幅回撤",
            "从高位大幅回撤",
            "高点大幅回撤",
            "从高点回撤",
            "冲高回落",
            "高位回撤",
            "大幅回撤",
        ),
    ),
)


@dataclass(frozen=True)
class PostLimitQueryContract:
    """Validated interpretation shared by planner, policy and execution."""

    version: str = POST_LIMIT_QUERY_VERSION
    mode: PostLimitMode = "screen"
    shape: PostLimitShape = "high_drawdown"
    shapes: tuple[PostLimitShape, ...] = ()
    data_as_of: date | None = None
    anchor_date: date | None = None
    recent_limit_days: int = 5
    statistics_days: int = 7
    symbol: str | None = None
    query: str | None = None
    min_peak_drawdown_pct: float | None = None
    max_volume_ratio: float | None = None
    max_range_pct: float | None = None
    min_anchor_change_pct: float | None = None
    max_anchor_change_pct: float | None = None
    board_height: int | None = None
    group_by: str | None = None
    sort_by: str | None = None
    sort_order: Literal["asc", "desc"] = "desc"
    limit: int = 10
    exhaustive: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["shapes"] = list(self.shapes)
        for field in ("data_as_of", "anchor_date"):
            value = payload[field]
            payload[field] = value.isoformat() if value else None
        return payload

    def to_tool_arguments(self) -> dict[str, Any]:
        payload = self.to_dict()
        payload.pop("version", None)
        payload.pop("mode", None)
        return payload


def looks_like_post_limit_question(message: str) -> bool:
    """Recognize event-relative price/volume research rather than a raw event list."""

    compact = re.sub(r"\s+", "", message.lower())
    if any(alias.replace(" ", "") in compact for _, aliases in SHAPE_ALIASES for alias in aliases):
        return True
    return any(term in compact for term in ("涨停后", "断板后", "涨停以来")) and any(
        term in compact
        for term in (
            "走势", "怎么走", "路径", "逐日", "回撤", "回调", "缩量", "横盘",
            "修复", "企稳", "统计", "表现", "观察",
        )
    )


def looks_like_post_limit_statistics_question(message: str) -> bool:
    compact = re.sub(r"\s+", "", message.lower())
    return looks_like_post_limit_question(message) and any(
        term in compact
        for term in (
            "统计", "历史表现", "正比例", "平均", "均值", "中位数", "样本数", "多少样本",
            "哪种形态", "比较", "胜率", "盈利", "成功率", "d+1", "d+3", "d+5",
        )
    )


def looks_like_post_limit_path_question(message: str) -> bool:
    compact = re.sub(r"\s+", "", message.lower())
    if looks_like_post_limit_statistics_question(message):
        return False
    entity_hint = bool(re.search(r"(?<!\d)\d{6}(?!\d)", message)) or any(
        term in compact for term in ("这只", "它", "该股", "这票")
    )
    named_prefix = re.search(r"([\u4e00-\u9fffA-Za-z]{2,10})涨停后", compact)
    if named_prefix and not any(
        named_prefix.group(1).endswith(term)
        for term in ("哪些", "有些", "股票", "涨停票", "近期", "最近")
    ):
        entity_hint = True
    return looks_like_post_limit_question(message) and entity_hint and any(
        term in compact for term in ("走势", "为什么", "怎么走", "路径", "逐日")
    )


def build_post_limit_query_contract(
    message: str,
    *,
    request_trade_date: date | None = None,
    planner_arguments: dict[str, Any] | None = None,
) -> PostLimitQueryContract:
    """Compile user wording; explicit text always overrides planner suggestions."""

    planner = dict(planner_arguments or {})
    mode: PostLimitMode = (
        "statistics" if looks_like_post_limit_statistics_question(message)
        else "path" if looks_like_post_limit_path_question(message)
        else _mode(planner.get("mode")) or "screen"
    )
    explicit_shapes = _extract_shapes(message)
    planner_shapes = _normalize_shapes(planner.get("shapes"))
    planner_shape = _shape(planner.get("shape"))
    shapes = explicit_shapes or planner_shapes or ((planner_shape,) if planner_shape else ())
    shape = shapes[0] if shapes else "high_drawdown"
    anchor_date = _extract_anchor_date(message) or _date(planner.get("anchor_date"))
    explicit_date = _extract_cutoff_date(message) or extract_trade_date(message)
    if mode == "path" and anchor_date == explicit_date and not _extract_cutoff_date(message):
        explicit_date = None
    data_as_of = explicit_date or request_trade_date or _date(planner.get("data_as_of"))

    recent_days = _extract_recent_days(message, statistics=False)
    statistics_days = _extract_recent_days(message, statistics=True)
    exhaustive = any(term in message for term in ("全部", "所有", "完整名单", "逐只"))
    explicit_limit = extract_result_limit(message)
    explicit_query = _extract_query_filter(message)
    explicit_board_height = _extract_board_height(message)
    explicit_group_by = _extract_group_by(message)
    explicit_sort_by = _extract_sort_by(message)
    explicit_sort_order = _extract_sort_order(message)

    peak_drawdown = _extract_percent_after(
        message, ("高位回撤", "高点回撤", "大幅回撤", "回撤"), ("以上", "至少", "不低于")
    )
    volume_ratio = _extract_number_after(message, ("量比",), ("以下", "以内", "不超过", "低于", "小于"))
    range_pct = _extract_percent_after(message, ("振幅", "区间幅度", "横盘幅度"), ("以下", "以内", "不超过"))

    return PostLimitQueryContract(
        mode=mode,
        shape=shape,
        shapes=shapes,
        data_as_of=data_as_of,
        anchor_date=anchor_date,
        recent_limit_days=_bounded_int(recent_days or planner.get("recent_limit_days"), 5, 1, 20),
        statistics_days=_bounded_int(statistics_days or planner.get("statistics_days"), 7, 1, 30),
        symbol=_text(planner.get("symbol")),
        query=explicit_query or _text(planner.get("query")),
        min_peak_drawdown_pct=peak_drawdown if peak_drawdown is not None else _float(planner.get("min_peak_drawdown_pct")),
        max_volume_ratio=volume_ratio if volume_ratio is not None else _float(planner.get("max_volume_ratio")),
        max_range_pct=range_pct if range_pct is not None else _float(planner.get("max_range_pct")),
        min_anchor_change_pct=_float(planner.get("min_anchor_change_pct")),
        max_anchor_change_pct=_float(planner.get("max_anchor_change_pct")),
        board_height=explicit_board_height or _bounded_optional_int(planner.get("board_height"), 1, 20),
        group_by=explicit_group_by or _enum(planner.get("group_by"), {"shape", "board_height", "anchor_age", "industry", "concept", "signal_date"}),
        sort_by=explicit_sort_by or _enum(planner.get("sort_by"), {"peak_drawdown_pct", "volume_ratio", "range_pct", "anchor_change_pct", "anchor_date", "symbol"}),
        sort_order=explicit_sort_order or _enum(planner.get("sort_order"), {"asc", "desc"}) or "desc",
        limit=100 if exhaustive else _bounded_int(explicit_limit or planner.get("limit"), 10, 1, 100),
        exhaustive=exhaustive,
    )


def _extract_shapes(message: str) -> tuple[PostLimitShape, ...]:
    compact = re.sub(r"\s+", "", message.lower())
    found = [shape for shape, aliases in SHAPE_ALIASES if any(alias.replace(" ", "") in compact for alias in aliases)]
    return tuple(dict.fromkeys(found))


def extract_trade_date(message: str) -> date | None:
    """Extract a full or shorthand date without importing the Agent package."""

    full = re.search(r"(20\d{2})[-/.年](\d{1,2})[-/.月](\d{1,2})", message)
    if full:
        return _safe_date(*(int(part) for part in full.groups()))
    short = re.search(r"(?<!\d)(\d{1,2})[./月](\d{1,2})(?:日|号)?", message)
    if short:
        month, day = (int(part) for part in short.groups())
        return _safe_date(date.today().year, month, day)
    return None


def extract_result_limit(message: str) -> int | None:
    """Extract an explicit bounded Top-N request."""

    match = re.search(r"(?:top\s*|前\s*)(\d{1,3})", message, re.IGNORECASE)
    if match is None:
        match = re.search(r"(?:列出|展示|给我)\s*(\d{1,3})\s*只", message)
    return max(1, min(int(match.group(1)), 100)) if match else None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _normalize_shapes(value: object) -> tuple[PostLimitShape, ...]:
    values = value if isinstance(value, list) else [value]
    result = [shape for item in values if (shape := _shape(item))]
    return tuple(dict.fromkeys(result))


def _shape(value: object) -> PostLimitShape | None:
    valid = {item[0] for item in SHAPE_ALIASES}
    text = _text(value)
    return text if text in valid else None  # type: ignore[return-value]


def _mode(value: object) -> PostLimitMode | None:
    text = _text(value)
    return text if text in {"screen", "path", "statistics"} else None  # type: ignore[return-value]


def _extract_recent_days(message: str, *, statistics: bool) -> int | None:
    compact = re.sub(r"\s+", "", message.lower())
    match = re.search(r"近(\d{1,2})个?(?:信号)?(?:交易日|日)", compact)
    if not match:
        return None
    if statistics:
        return int(match.group(1)) if looks_like_post_limit_statistics_question(message) else None
    return None if looks_like_post_limit_statistics_question(message) else int(match.group(1))


def _extract_anchor_date(message: str) -> date | None:
    labeled = re.search(
        r"(?:锚点|涨停日)[为是:]?\s*((?:20\d{2}[-/.年])?\d{1,2}[-/.月]\d{1,2}(?:日|号)?)",
        message,
    )
    if labeled:
        return extract_trade_date(labeled.group(1))
    anchored = re.search(
        r"(?:从|以)?\s*((?:20\d{2}[-/.年])?\d{1,2}[-/.月]\d{1,2}(?:日|号)?)"
        r"(?:的)?(?:涨停|封板)(?:日)?(?:开始|以来|后)",
        message,
    )
    return extract_trade_date(anchored.group(1)) if anchored else None


def _extract_cutoff_date(message: str) -> date | None:
    match = re.search(
        r"(?:截至|截止|数据截至)[为到:]?\s*"
        r"((?:20\d{2}[-/.年])?\d{1,2}[-/.月]\d{1,2}(?:日|号)?)",
        message,
    )
    return extract_trade_date(match.group(1)) if match else None


def _extract_board_height(message: str) -> int | None:
    if match := re.search(r"(?<!\d)(\d{1,2})\s*板(?!块)", message):
        return _bounded_optional_int(match.group(1), 1, 20)
    chinese = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    match = re.search(r"([一二三四五六七八九])板(?!块)", message)
    return chinese.get(match.group(1)) if match else None


def _extract_query_filter(message: str) -> str | None:
    patterns = (
        r"(?:题材|概念|行业)[为是:]\s*([\u4e00-\u9fffA-Za-z0-9_-]{2,12})",
        r"([\u4e00-\u9fffA-Za-z0-9_-]{2,12})(?:题材|概念|行业)(?:的|中|里)?",
    )
    for pattern in patterns:
        if match := re.search(pattern, message):
            value = match.group(1)
            for prefix in ("有哪些", "哪些", "筛选", "查找", "看看", "找出", "找"):
                if value.startswith(prefix):
                    value = value[len(prefix):]
                    break
            return value or None
    return None


def _extract_group_by(message: str) -> str | None:
    compact = re.sub(r"\s+", "", message)
    aliases = (
        ("signal_date", ("按信号日期", "按日期")),
        ("anchor_age", ("按距涨停天数", "按锚点天数", "按整理天数")),
        ("board_height", ("按板高", "按连板高度", "按板数")),
        ("industry", ("按行业",)),
        ("concept", ("按题材", "按概念")),
        ("shape", ("按形态", "按策略")),
    )
    return next((key for key, terms in aliases if any(term in compact for term in terms)), None)


def _extract_sort_by(message: str) -> str | None:
    compact = re.sub(r"\s+", "", message)
    aliases = (
        ("peak_drawdown_pct", ("按回撤", "回撤排序", "回撤最大", "回撤最小")),
        ("volume_ratio", ("按量比", "量比排序", "量比最低", "量比最高")),
        ("range_pct", ("按振幅", "振幅排序", "振幅最小", "振幅最大")),
        ("anchor_change_pct", ("按涨跌幅", "涨跌幅排序", "涨幅最高", "跌幅最大")),
        ("anchor_date", ("按涨停日", "按锚点日期")),
        ("symbol", ("按代码",)),
    )
    return next((key for key, terms in aliases if any(term in compact for term in terms)), None)


def _extract_sort_order(message: str) -> Literal["asc", "desc"] | None:
    compact = re.sub(r"\s+", "", message)
    if any(term in compact for term in ("升序", "从低到高", "最小优先", "最低优先", "量比最低", "振幅最小", "回撤最小")):
        return "asc"
    if any(term in compact for term in ("降序", "从高到低", "最大优先", "最高优先", "回撤最大", "量比最高", "振幅最大", "涨幅最高", "跌幅最大")):
        return "desc"
    return None


def _extract_percent_after(message: str, names: tuple[str, ...], bounds: tuple[str, ...]) -> float | None:
    joined_names = "|".join(map(re.escape, names))
    joined_bounds = "|".join(map(re.escape, bounds))
    patterns = (
        rf"(?:{joined_names})[^\d]{{0,8}}(\d+(?:\.\d+)?)\s*%(?:{joined_bounds})?",
        rf"(\d+(?:\.\d+)?)\s*%(?:{joined_bounds})[^，。；]{{0,6}}(?:{joined_names})",
    )
    for pattern in patterns:
        if match := re.search(pattern, message, re.IGNORECASE):
            return max(0.0, min(float(match.group(1)), 50.0))
    return None


def _extract_number_after(message: str, names: tuple[str, ...], bounds: tuple[str, ...]) -> float | None:
    joined_names = "|".join(map(re.escape, names))
    joined_bounds = "|".join(map(re.escape, bounds))
    match = re.search(rf"(?:{joined_names})[^\d]{{0,6}}(\d+(?:\.\d+)?)(?:{joined_bounds})?", message)
    return max(0.0, min(float(match.group(1)), 5.0)) if match else None


def _date(value: object) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def _text(value: object) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def _float(value: object) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _bounded_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        return max(minimum, min(int(value), maximum))
    except (TypeError, ValueError):
        return default


def _bounded_optional_int(value: object, minimum: int, maximum: int) -> int | None:
    return _bounded_int(value, minimum, minimum, maximum) if value is not None else None


def _enum(value: object, allowed: set[str]) -> str | None:
    text = _text(value)
    return text if text in allowed else None

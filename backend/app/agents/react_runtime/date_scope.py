"""Semantic query dates shared by input review and deterministic tool policy."""

from datetime import date, timedelta
from typing import Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TradingDayComparison(BaseModel):
    """An additional comparison observation, not a guessed calendar date."""

    model_config = ConfigDict(extra="forbid")

    anchor_date: date = Field(description="The explicitly requested observation being compared; it must also be in dates.")
    offset: int = Field(ge=-20, le=20, strict=True,
        description="Nonzero exchange-session offset: previous trading day=-1, next trading day=1. Not a historical lookback window.")

    @model_validator(mode="after")
    def nonzero(self):
        if self.offset == 0:
            raise ValueError("Trading-day comparison offset must be nonzero")
        return self


class DateScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["unspecified", "dates", "range"] = Field(default="unspecified",
        description="unspecified has no dates or endpoints; dates has one or more explicit dates and no endpoints; range requires BOTH explicit endpoints and no discrete dates. A past-N-trading-days calculation is not a date range: keep N in current_task, using dates for an explicit as-of date, otherwise unspecified. Never guess the window's start date.")
    basis: Literal["observation", "event"] = Field(default="observation",
        description="observation: dates describe data snapshots/as-of queries. event: dates select an anchor event or cohort whose subsequent outcome is being studied, not the outcome cutoff.")
    dates: list[date] = Field(default_factory=list, max_length=16,
        description="Requested dates in the declared basis: observation dates or event dates. One date for a single day; all comparison dates for a multi-day task. Not every historical bar used for a calculation.")
    start_date: date | None = Field(default=None,
        description="Required only for range, together with end_date; null otherwise. Not an inferred lookback start.")
    end_date: date | None = Field(default=None,
        description="Required only for range, together with start_date; null otherwise. A lone as-of date belongs in dates.")
    observation_cutoff: date | None = Field(default=None,
        description="An explicitly requested cutoff for observing events' later outcomes. Never infer it by adding calendar days to a trading-day horizon.")
    trading_day_comparisons: list[TradingDayComparison] = Field(default_factory=list, max_length=8,
        description="Additional observations explicitly requested relative to a listed date, e.g. comparison with its previous trading day. Do not guess these dates or add them to dates; the server resolves exchange sessions. Empty for lookback calculations, event outcomes, negated or superseded comparisons.")

    @model_validator(mode="after")
    def consistent(self):
        if self.mode == "dates":
            if not self.dates or len(set(self.dates)) != len(self.dates):
                raise ValueError("Date scope requires distinct observation dates")
            if self.start_date is not None or self.end_date is not None:
                raise ValueError("Discrete dates cannot also declare a range")
        elif self.mode == "range":
            if self.dates or self.start_date is None or self.end_date is None:
                raise ValueError("Date range requires both endpoints and no discrete dates")
            if self.start_date > self.end_date:
                raise ValueError("Date range start must not follow its end")
        elif self.dates or self.start_date is not None or self.end_date is not None:
            raise ValueError("Unspecified date scope cannot contain dates")
        if self.trading_day_comparisons:
            if self.mode != "dates" or self.basis != "observation":
                raise ValueError("Trading-day comparisons require discrete observation dates")
            if any(item.anchor_date not in self.dates for item in self.trading_day_comparisons):
                raise ValueError("Comparison anchor must be a requested observation date")
        if self.observation_cutoff is not None:
            if self.basis != "event":
                raise ValueError("A separate observation cutoff requires event-date semantics")
            latest = max(self.dates) if self.dates else self.end_date
            if latest is not None and self.observation_cutoff < latest:
                raise ValueError("Observation cutoff cannot precede the requested events")
        return self

    def resolve_comparisons(self, calendar: Callable[[date, date], list[date]]) -> tuple["DateScope", list[str]]:
        """Resolve only declared comparisons using verified exchange sessions."""
        if not self.trading_day_comparisons:
            return self, []
        anchors = [item.anchor_date for item in self.trading_day_comparisons]
        # The window is a bounded calendar lookup, never an assumed trading-day
        # conversion. Insufficient coverage is a real missing-data condition.
        try:
            days = calendar(min(anchors) - timedelta(days=90), max(anchors) + timedelta(days=90))
            if any(type(day) is not date for day in days):
                raise ValueError("Invalid exchange-calendar entry")
            days = sorted(set(days))
        except Exception:
            days = []
        resolved, missing = list(self.dates), []
        for item in self.trading_day_comparisons:
            candidates = [day for day in days if (day < item.anchor_date if item.offset < 0 else day > item.anchor_date)]
            if not days or not days[0] <= item.anchor_date <= days[-1] or len(candidates) < abs(item.offset):
                direction = "前" if item.offset < 0 else "后"
                missing.append(f"交易日历暂不可用或覆盖不足，无法确认{item.anchor_date.isoformat()}的{direction}{abs(item.offset)}个交易日比较日期。")
                continue
            selected = candidates[item.offset] if item.offset < 0 else candidates[item.offset - 1]
            if selected not in resolved:
                if len(resolved) >= 16:
                    missing.append("本轮比较日期过多，无法解析全部比较日期；请缩小日期范围。")
                    continue
                resolved.append(selected)
        scope = self.model_copy(update={"dates": resolved})
        return scope, missing

    @property
    def single_date(self) -> date | None:
        return self.dates[0] if self.mode == "dates" and len(self.dates) == 1 else None

    def bind(self, fields: tuple[str, ...], arguments: dict) -> dict:
        """Bind observation dates without confusing them with lookback history."""
        if self.mode == "unspecified":
            return arguments
        args = dict(arguments)
        for field in fields:
            # Historical windows/anchor events may precede a single requested
            # observation date. They are not an additional as-of observation.
            if field in {"start_date", "anchor_date"} and self.basis != "event":
                if self.mode == "range" and field == "start_date":
                    self._bind_range(args, field, self.start_date)
                continue
            value = args.get(field)
            if self.single_date is not None:
                self._bind_exact(args, field, self.single_date)
            elif self.mode == "dates":
                if value is None:
                    raise ValueError(f"{field} must select an explicit date from the task's comparison dates")
                if self._date(value) not in self.dates:
                    raise ValueError(f"{field} conflicts with the task's comparison dates")
            elif field in {"start_date", "end_date", "data_as_of", "requested_as_of"}:
                self._bind_range(args, field, self.start_date if field == "start_date" else self.end_date)
            else:
                if value is None:
                    raise ValueError(f"{field} must select an explicit date within the task's date range")
                if not self.start_date <= self._date(value) <= self.end_date:
                    raise ValueError(f"{field} is outside the task's date range")
        # Keep valid lookback windows, but never allow a window to run backwards.
        end = args.get("end_date") or args.get("data_as_of")
        if end is not None:
            for field in ("start_date", "anchor_date"):
                if args.get(field) is not None and self._date(args[field]) > self._date(end):
                    raise ValueError(f"{field} must not follow the observation date")
        return args

    @staticmethod
    def _date(value) -> date:
        if not isinstance(value, str):
            raise ValueError("Tool dates must be ISO date strings")
        return date.fromisoformat(value)

    @classmethod
    def _bind_exact(cls, args: dict, field: str, required: date):
        if args.get(field) is not None and cls._date(args[field]) != required:
            raise ValueError(f"{field} conflicts with the task date {required.isoformat()}")
        args[field] = required.isoformat()

    def _bind_range(self, args: dict, field: str, default: date):
        value = args.get(field)
        if value is not None and not self.start_date <= self._date(value) <= self.end_date:
            raise ValueError(f"{field} is outside the task's date range")
        if value is None:
            args[field] = default.isoformat()

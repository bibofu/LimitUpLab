"""Semantic query dates shared by input review and deterministic tool policy."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DateScope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["unspecified", "dates", "range"] = "unspecified"
    basis: Literal["observation", "event"] = Field(default="observation",
        description="observation: dates describe data snapshots/as-of queries. event: dates select an anchor event or cohort whose subsequent outcome is being studied, not the outcome cutoff.")
    dates: list[date] = Field(default_factory=list, max_length=16,
        description="Requested dates in the declared basis: observation dates or event dates. One date for a single day; all comparison dates for a multi-day task. Not every historical bar used for a calculation.")
    start_date: date | None = None
    end_date: date | None = None
    observation_cutoff: date | None = Field(default=None,
        description="An explicitly requested cutoff for observing events' later outcomes. Never infer it by adding calendar days to a trading-day horizon.")

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
        if self.observation_cutoff is not None:
            if self.basis != "event":
                raise ValueError("A separate observation cutoff requires event-date semantics")
            latest = max(self.dates) if self.dates else self.end_date
            if latest is not None and self.observation_cutoff < latest:
                raise ValueError("Observation cutoff cannot precede the requested events")
        return self

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

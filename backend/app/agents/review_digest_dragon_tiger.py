"""Attach exact-first-board-date positive evidence without treating absence as false."""

from datetime import date

from app.review_digest_models import DigestStock


_PLACEHOLDERS = {"", "none", "null", "unknown", "unknown-source", "n/a", "na", "未知", "缺失", "未采集", "待核验", "数据不足", "-", "--", "—"}
_UNVERIFIED = "首板当天龙虎榜未核验（未保存完整榜单覆盖）"
_NO_DATE = "首板当天龙虎榜未核验（首板日期未确认）"
_NO_SOURCE = "首板当天龙虎榜未核验（上榜标记缺少有效来源）"
_READ_FAILED = "首板当天龙虎榜未核验（本地快照读取失败）"
_OWN_MISSING = {_UNVERIFIED, _NO_DATE, _NO_SOURCE, _READ_FAILED}


def attach_first_board_dragon_tiger(
    stocks: list[DigestStock], repository, predictions: dict | None = None,
) -> None:
    """Update display stocks only; never collect data or alter saved facts.

    Existing storage has no complete-list coverage proof. A False flag therefore
    means unknown here. An explicit True plus a named source confirms only a
    listing record; its amounts may span multiple sessions and are not exposed.
    """
    by_date: dict[date, list | None] = {}
    for stock in stocks:
        stock.first_dragon_tiger_on_list = None
        stock.first_dragon_tiger_source = None
        stock.first_dragon_tiger_reason = None
        stock.data_missing = [item for item in stock.data_missing if item not in _OWN_MISSING]
        if stock.first_board_date is None:
            _missing(stock, _NO_DATE)
            continue
        prediction = (predictions or {}).get((stock.first_board_date, stock.symbol))
        saved = _prediction_enrichment(prediction, stock)
        if _confirmed(saved):
            _apply_positive(stock, saved)
            continue
        day = stock.first_board_date
        if day not in by_date:
            try:
                by_date[day] = list(repository.list_enrichment_for_date(day))
            except Exception:
                by_date[day] = None
        snapshots = [row for row in by_date[day] or [] if _same_identity(row, stock, required=True)]
        confirmed = next((row for row in snapshots if _confirmed(row)), None)
        if confirmed is not None:
            _apply_positive(stock, confirmed)
            continue
        evidence = [saved, *snapshots]
        stock.first_dragon_tiger_reason = next(
            (reason for row in evidence if (reason := _text(_field(row, "dragon_tiger_reason"))) is not None), None,
        )
        if any(_field(row, "dragon_tiger_on_list") is True for row in evidence):
            _missing(stock, _NO_SOURCE)
        else:
            _missing(stock, _READ_FAILED if by_date[day] is None else _UNVERIFIED)


def _field(record, name):
    return record.get(name) if isinstance(record, dict) else getattr(record, name, None)


def _text(value) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if value.lower() not in _PLACEHOLDERS else None


def _same_identity(record, stock: DigestStock, *, required: bool = False) -> bool:
    for field, expected in (("trade_date", stock.first_board_date), ("symbol", stock.symbol)):
        observed = _field(record, field)
        if observed is None:
            if required:
                return False
            continue
        if field == "trade_date" and isinstance(observed, str):
            try:
                observed = date.fromisoformat(observed)
            except ValueError:
                return False
        if observed != expected:
            return False
    return True


def _prediction_enrichment(prediction, stock: DigestStock):
    if prediction is None or not _same_identity(prediction, stock):
        return None
    facts = _field(prediction, "facts_json")
    if not isinstance(facts, dict) or not _same_identity(facts, stock):
        return None
    enrichment = facts.get("enrichment")
    return enrichment if isinstance(enrichment, dict) and _same_identity(enrichment, stock) else None


def _confirmed(evidence) -> bool:
    return _field(evidence, "dragon_tiger_on_list") is True and _text(_field(evidence, "dragon_tiger_source")) is not None


def _apply_positive(stock: DigestStock, evidence) -> None:
    stock.first_dragon_tiger_on_list = True
    stock.first_dragon_tiger_source = _text(_field(evidence, "dragon_tiger_source"))
    stock.first_dragon_tiger_reason = _text(_field(evidence, "dragon_tiger_reason"))


def _missing(stock: DigestStock, reason: str) -> None:
    if reason not in stock.data_missing:
        stock.data_missing.append(reason)

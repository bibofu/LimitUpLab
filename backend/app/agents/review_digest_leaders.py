"""Cutoff-bound market leader episodes and their first-to-second-board facts."""

from collections import defaultdict
from datetime import time
from types import SimpleNamespace

from app.agents.review_market_leaders import _build_profiles, _finite, _leader_episodes
from app.agents.review_digest_prices import next_open_fact
from app.review_digest_models import DigestStock


def _unique_rows(rows, fields):
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row.trade_date, row.symbol)].append(row)
    unique, conflicts = {}, {}
    for key, values in grouped.items():
        signatures = {tuple(getattr(row, field, None) for field in fields) for row in values}
        if len(signatures) == 1:
            unique[key] = values[0]
        else:
            conflicts[key] = values
    return unique, conflicts


def _episode_key(symbol, day, height, calendar, indices):
    index = indices.get(day)
    offset = index - height + 1 if index is not None else None
    anchor = calendar[offset] if offset is not None and offset >= 0 else offset
    return symbol, anchor if anchor is not None else day


def _episodes(by_case, conflicts, calendar, indices, end_date):
    start = calendar[max(0, len(calendar) - 5)]
    entries = _leader_episodes({key: row for key, row in by_case.items() if key[0] in indices},
                              calendar, indices, start, end_date)
    keyed = {
        ((row["symbol"], row["first_board_date"]) if row["first_board_date"] is not None else
         _episode_key(row["symbol"], row["latest_date"], row["max_board_height"], calendar, indices)): row
        for row in entries
    }
    # Conflicting source rows cannot certify a chain, but still disclose a reported leader.
    for (day, symbol), rows in sorted(conflicts.items()):
        if day not in calendar[-5:]:
            continue
        reported = sorted((row for row in rows if row.closed_limit and row.board_height >= 3),
                          key=lambda row: (row.board_height, row.name))
        if not reported:
            continue
        possible_keys = {_episode_key(symbol, day, row.board_height, calendar, indices) for row in reported}
        matches = {id(keyed[key]): keyed[key] for key in possible_keys if key in keyed}
        verified = [entry for entry in matches.values() if entry["first_board_date"] is not None]
        if len(verified) == 1:
            entry = verified[0]
        elif not verified and matches:
            entry = min(matches.values(), key=lambda item: (item["latest_date"], item["max_board_height"]))
            entry["data_missing"] = [missing for item in matches.values() for missing in item["data_missing"]]
            entry["max_board_height"] = max(item["max_board_height"] for item in matches.values())
            keyed = {key: entry if id(value) in matches else value for key, value in keyed.items()}
        else:
            entry = {
                "symbol": symbol, "name": reported[0].name, "first_board_date": None,
                "max_board_height": reported[-1].board_height, "latest_date": day, "data_missing": [],
            }
            keyed[(symbol, ("conflict", day))] = entry
        if entry["first_board_date"] is None:
            # Alternative source heights are aliases of one uncertain case, not distinct cycles.
            entry["max_board_height"] = max(entry["max_board_height"], reported[-1].board_height)
            entry["latest_date"] = max(entry["latest_date"], day)
            for key in possible_keys:
                if key not in keyed or keyed[key]["first_board_date"] is None:
                    keyed[key] = entry
        for row in reported:
            entry["data_missing"].append(f"{day} 事件记录冲突，来源报{row.board_height}板未核验")
    return list({id(entry): entry for entry in keyed.values()}.values())


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() not in {"", "未知", "--", "未分类"} else None


def _concepts(value):
    text = _text(value)
    if not text:
        return []
    for delimiter in ("，", "、", ";", "；", "|", "+"):
        text = text.replace(delimiter, ",")
    return sorted({part.strip() for part in text.split(",") if _text(part)})


def _clock(value):
    return value.strftime("%H:%M") if isinstance(value, time) and value != time(0) else None


def _second_board(stock, first_bar, second_bar, second):
    stock.first_close = _finite(getattr(first_bar, "close", None), positive=True)
    stock.second_open_pct = stock.next_open_pct
    if stock.second_open_pct is None:
        stock.data_missing.append("首板收盘或二板开盘K线缺失，无法计算二板开盘涨幅")
    stock.second_limit_time = _clock(second.first_limit_time)
    breaks = _finite(second.break_count)
    stock.second_break_count = int(breaks) if breaks is not None else None
    stock.second_turnover_rate = _finite(second.turnover_rate, positive=True)
    for value, label in ((stock.second_limit_time, "二板首次封板时间"),
                         (stock.second_break_count, "二板炸板次数"),
                         (stock.second_turnover_rate, "二板换手率")):
        if value is None:
            stock.data_missing.append(label + "缺失")
    values = [_finite(getattr(second_bar, field, None), positive=True) for field in ("open", "high", "low", "close")]
    if all(value is not None for value in values):
        opened, high, low, close = values
        if low <= min(opened, close) <= max(opened, close) <= high:
            stock.second_board_shape = "一字板" if opened == high == low == close else "有价格波动"
    if stock.second_board_shape is None:
        stock.data_missing.append("二板完整合法OHLC缺失，无法判断一字或价格波动形态")


def build_digest_leaders(*, events, repository, end_date, trade_dates) -> tuple[list[DigestStock], list[str]]:
    calendar = sorted({day for day in trade_dates if day <= end_date})
    notes = [
        "范围为含截止日在内最近5个交易日出现过三板及以上的连板轮次，包含窗口前已达三板者；同股不同轮次分别计数。",
        "二板特征仅描述已达到三板的样本如何晋级，不计算这一事后赢家组必然为100%的1进2成功率。",
        "本地事件覆盖不保证全市场完整；缺日或冲突不能证实连板，未知首板不进入首板特征统计。",
    ]
    if not calendar:
        return [], notes + ["交易日历缺失，无法确定近5个交易日窗口。"]
    if len(calendar) < 5:
        notes.append(f"交易日历仅覆盖{len(calendar)}个交易日，窗口不足5日。")
    indices = {day: index for index, day in enumerate(calendar)}
    fields = ("closed_limit", "board_height", "first_limit_time", "break_count", "turnover_rate", "industry", "concept")
    by_case, conflicts = _unique_rows((event for event in events if event.trade_date <= end_date), fields)
    window = calendar[-5:]
    if any(window[0] <= day <= end_date and day not in indices for day, _ in by_case):
        notes.append("观察期间存在未列入已核验交易日历的事件日期，未将其计入近5日范围。")
    missing_days = set(window) - {day for day, _ in by_case} - {day for day, _ in conflicts}
    if missing_days:
        notes.append("观察窗整日事件缺失：" + "、".join(map(str, sorted(missing_days))) + "；不以更早交易日补位。")
    episodes = _episodes(by_case, conflicts, calendar, indices, end_date)
    feature_events = {(row["first_board_date"], row["symbol"]): by_case[(row["first_board_date"], row["symbol"])]
                      for row in episodes if row["first_board_date"] is not None}
    symbols = sorted({symbol for _, symbol in feature_events})
    bars, bar_conflicts = _unique_rows(
        (bar for bar in repository.list_daily_bars_for_symbols(symbols, end_date=end_date)
         if bar.trade_date <= end_date and bar.symbol in symbols) if symbols else [],
        ("open", "high", "low", "close"),
    )
    cached_repository = SimpleNamespace(
        list_enrichment_for_date=repository.list_enrichment_for_date,
        list_daily_bars_for_symbols=lambda requested, **_: [bar for (_, symbol), bar in bars.items() if symbol in requested],
    )
    profiles = _build_profiles(feature_events, cached_repository, end_date)
    stocks = []
    for row in episodes:
        anchor, symbol = row["first_board_date"], row["symbol"]
        stock = DigestStock(symbol=symbol, name=row["name"], first_board_date=anchor,
                            max_board_height=row["max_board_height"] if anchor is not None else None,
                            data_missing=[item for item in row["data_missing"] if item != "首板日在本期观察窗外"])
        if anchor is None:
            stock.data_missing.append(f"首板锚点未核验，来源报{row['max_board_height']}板；不填入首板及二板特征")
        else:
            first, profile = feature_events[(anchor, symbol)], profiles[(anchor, symbol)]
            for field in ("position_label", "float_market_cap", "first_limit_time", "break_count", "turnover_rate"):
                setattr(stock, field, profile[field])
            stock.industry, stock.concepts = _text(first.industry), _concepts(first.concept)
            stock.data_missing.extend(profile["data_missing"])
            if not stock.industry:
                stock.data_missing.append("首板行业缺失")
            if not stock.concepts:
                stock.data_missing.append("首板题材缺失")
            second_day = calendar[indices[anchor] + 1]
            second = by_case[(second_day, symbol)]
            stock.second_board_date = second_day
            exact_bars = [bar for key in ((anchor, symbol), (second_day, symbol)) if (bar := bars.get(key)) is not None]
            stock.next_trade_date, stock.next_open_pct, next_missing = next_open_fact(
                bars=exact_bars, symbol=symbol, first_board_date=anchor,
                trade_dates=calendar, end_date=end_date,
            )
            stock.data_missing.extend(next_missing)
            _second_board(stock, bars.get((anchor, symbol)), bars.get((second_day, symbol)), second)
            for day in (anchor, second_day):
                if (day, symbol) in bar_conflicts:
                    stock.data_missing.append(f"{day} K线记录冲突")
        stock.data_missing = sorted(set(stock.data_missing))
        stocks.append(stock)
    recomputed = sum(profile["position_source"] == "recomputed_local_daily_bars" for profile in profiles.values())
    if recomputed:
        notes.append(f"{recomputed}个首板位置由截至首板日的本地K线重算，并非当时保存的分类；至少21根，历史长度与未复权行情仍有限制。")
    estimated = sum(profile["float_market_cap_source"] == "derived_from_amount_and_turnover" for profile in profiles.values())
    if estimated:
        notes.append(f"{estimated}个首板市值快照原由成交额/换手率推算，是当日估计值而非实测流通市值；本次未新增估算。")
    if any(profile["float_market_cap"] is not None and not profile["float_market_cap_source"] for profile in profiles.values()):
        notes.append("部分首板市值快照缺少来源标识，不能默认视为实测值。")
    if conflicts:
        notes.append(f"{len(conflicts)}个同股同日事件存在冲突，冲突记录未用于核验连板或补齐特征。")
    return sorted(stocks, key=lambda stock: (-(stock.max_board_height or 0), stock.symbol, stock.first_board_date or end_date)), notes

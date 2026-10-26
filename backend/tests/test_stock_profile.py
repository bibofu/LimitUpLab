"""Company-profile normalization keeps CNInfo and market-cap facts separate."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services.stock_profile import (
    BUSINESS_SCOPE_LIMIT,
    PROFILE_TEXT_LIMIT,
    SHANGHAI,
    build_stock_profile,
)


NOW = datetime(2026, 10, 26, 15, 0, tzinfo=SHANGHAI)


def cninfo_profile(**updates):
    values = {
        "公司名称": "贵州茅台酒股份有限公司",
        "A股代码": "600519",
        "A股简称": "贵州茅台",
        "所属市场": "上交所",
        "所属行业": "酒、饮料和精制茶制造业",
        "主营业务": "贵州茅台酒系列产品的产品研制、酿造生产、包装和销售。",
        "经营范围": "茅台酒系列产品的生产与销售；饮料、食品、包装材料的生产、销售。",
        "上市日期": "2001-08-27",
        "成立日期": "1999-11-20",
        "注册资金": "125008.1601",
        "法人代表": "陈华",
        "注册地址": "贵州省仁怀市茅台镇",
        "办公地址": "贵州省仁怀市茅台镇",
        "官方网站": "www.moutaichina.com",
    }
    values.update(updates)
    return values


def eastmoney_info(**updates):
    values = {
        "股票代码": "600519",
        "股票简称": "贵州茅台",
        "总股本": "1256197800",
        "流通股": "1256197800",
        "行业": "酿酒行业",
        "总市值": "1696000000000",
        "流通市值": "1696000000000",
        "上市时间": "2001-08-27",
        "最新": "1349.98",
    }
    values.update(updates)
    return values


def loader(value):
    return lambda symbol: value


def raiser(error):
    def load(symbol):
        raise error
    return load


def test_profile_and_market_cap_are_merged_with_sources_and_units():
    payload = build_stock_profile(
        "600519",
        profile_loader=loader(cninfo_profile()),
        market_cap_loader=loader(eastmoney_info()),
        now=NOW,
    )

    assert payload["status"] == "ok"
    assert payload["name"] == "贵州茅台"
    assert payload["company_name"] == "贵州茅台酒股份有限公司"
    assert payload["industry"] == "酒、饮料和精制茶制造业"
    assert payload["industry_source"] == "cninfo"
    assert payload["industry_alternatives"] == [
        {"value": "酿酒行业", "source": "eastmoney"}
    ]
    assert payload["main_business"].startswith("贵州茅台酒")
    assert payload["registered_capital"] == pytest.approx(125008.1601)
    assert payload["registered_capital_unit"] == "万元"
    assert payload["total_market_cap"] == pytest.approx(1_696_000_000_000)
    assert payload["float_market_cap"] == pytest.approx(1_696_000_000_000)
    assert payload["total_shares"] == pytest.approx(1_256_197_800)
    assert payload["market_cap_as_of"] == "2026-10-26T15:00:00+08:00"
    assert payload["profile_as_of"] == "2026-10-26"
    assert payload["sources"]["profile"].endswith("stock_profile_cninfo")
    assert payload["sources"]["market_cap"].endswith("stock_individual_info_em")
    assert payload["data_missing"] == []
    assert payload["truncated"] is False


def test_market_cap_failure_keeps_profile_and_reports_explicit_gaps():
    payload = build_stock_profile(
        "600519",
        profile_loader=loader(cninfo_profile()),
        market_cap_loader=raiser(TimeoutError("eastmoney timed out")),
        now=NOW,
    )

    assert payload["status"] == "partial"
    assert payload["profile_status"] == "ok"
    assert payload["market_cap_status"] == "unavailable"
    assert payload["main_business"]
    assert payload["total_market_cap"] is None
    assert payload["float_market_cap"] is None
    assert "total_market_cap:eastmoney_unavailable" in payload["data_missing"]
    assert "total_shares:eastmoney_unavailable" in payload["data_missing"]
    assert any("eastmoney" in item for item in payload["source_errors"])
    assert payload["market_cap_as_of"] is None


def test_profile_failure_keeps_market_cap_and_falls_back_to_eastmoney_industry():
    payload = build_stock_profile(
        "600519",
        profile_loader=raiser(RuntimeError("cninfo failed")),
        market_cap_loader=loader(eastmoney_info()),
        now=NOW,
    )

    assert payload["status"] == "partial"
    assert payload["profile_status"] == "unavailable"
    assert payload["market_cap_status"] == "ok"
    assert payload["industry"] == "酿酒行业"
    assert payload["industry_source"] == "eastmoney"
    assert payload["main_business"] is None
    assert "profile:cninfo_unavailable" in payload["data_missing"]
    assert payload["total_market_cap"] == pytest.approx(1_696_000_000_000)


def test_both_provider_errors_return_error_without_invented_values():
    payload = build_stock_profile(
        "600519",
        profile_loader=raiser(RuntimeError("cninfo failed")),
        market_cap_loader=raiser(TimeoutError("eastmoney timed out")),
        now=NOW,
    )

    assert payload["status"] == "error"
    assert payload["name"] == "600519"
    assert payload["registered_capital"] is None
    assert payload["total_market_cap"] is None
    assert len(payload["source_errors"]) == 2
    assert "profile:cninfo_unavailable" in payload["data_missing"]


def test_both_empty_responses_return_empty_not_error():
    payload = build_stock_profile(
        "600519",
        profile_loader=loader({}),
        market_cap_loader=loader({}),
        now=NOW,
    )

    assert payload["status"] == "empty"
    assert payload["profile_status"] == "empty"
    assert payload["market_cap_status"] == "empty"
    assert payload["name"] == "600519"


def test_long_profile_text_is_truncated_and_marked():
    payload = build_stock_profile(
        "600519",
        profile_loader=loader(cninfo_profile(
            **{"主营业务": "主" * (PROFILE_TEXT_LIMIT + 50),
               "经营范围": "营" * (BUSINESS_SCOPE_LIMIT + 50)},
        )),
        market_cap_loader=loader(eastmoney_info()),
        now=NOW,
    )

    assert payload["truncated"] is True
    assert payload["truncated_fields"] == ["main_business", "business_scope"]
    assert len(payload["main_business"]) == PROFILE_TEXT_LIMIT
    assert len(payload["business_scope"]) == BUSINESS_SCOPE_LIMIT


def test_symbol_mismatch_discards_provider_values_instead_of_mixing_stocks():
    payload = build_stock_profile(
        "600519",
        profile_loader=loader(cninfo_profile()),
        market_cap_loader=loader(eastmoney_info(**{"股票代码": "000001"})),
        now=NOW,
    )

    assert payload["status"] == "partial"
    assert payload["total_market_cap"] is None
    assert "total_market_cap:eastmoney_unavailable" in payload["data_missing"]
    assert any("SymbolMismatch" in item for item in payload["source_errors"])


def test_bse_symbol_keeps_profile_when_eastmoney_is_unavailable():
    payload = build_stock_profile(
        "830505",
        profile_loader=loader(cninfo_profile(
            **{"A股代码": "830505", "A股简称": "岭南精工", "所属市场": None},
        )),
        market_cap_loader=raiser(RuntimeError("unsupported market code")),
        now=NOW,
    )

    assert payload["status"] == "partial"
    assert payload["market"] == "北交所"
    assert payload["name"] == "岭南精工"
    assert payload["total_market_cap"] is None


def test_industry_falls_back_to_eastmoney_only_when_cninfo_has_none():
    payload = build_stock_profile(
        "600519",
        profile_loader=loader(cninfo_profile(**{"所属行业": None})),
        market_cap_loader=loader(eastmoney_info()),
        now=NOW,
    )

    assert payload["industry"] == "酿酒行业"
    assert payload["industry_source"] == "eastmoney"
    assert payload["industry_alternatives"] == []


@pytest.mark.parametrize("value", ["", "60051", "600519.SH", "贵州茅台", "ABCDEF"])
def test_invalid_symbol_is_rejected_before_provider_calls(value):
    with pytest.raises(ValueError):
        build_stock_profile(
            value,
            profile_loader=loader(cninfo_profile()),
            market_cap_loader=loader(eastmoney_info()),
            now=NOW,
        )


def test_accepts_exchange_prefix_and_normalizes_symbol():
    payload = build_stock_profile(
        "SH600519",
        profile_loader=loader(cninfo_profile()),
        market_cap_loader=loader(eastmoney_info()),
        now=NOW,
    )

    assert payload["symbol"] == "600519"


def test_market_cap_is_not_fetched_for_profile_only_queries():
    called = []

    def market_cap_loader(symbol):
        called.append(symbol)
        raise AssertionError("market-cap loader must not run for a profile-only query")

    payload = build_stock_profile(
        "600519",
        include_market_cap=False,
        profile_loader=loader(cninfo_profile()),
        market_cap_loader=market_cap_loader,
        now=NOW,
    )

    assert called == []
    assert payload["status"] == "ok"
    assert payload["market_cap_status"] == "not_requested"
    assert payload["market_cap_as_of"] is None
    assert not any("market_cap" in item or "shares" in item for item in payload["data_missing"])


def test_numeric_suffixes_are_normalized_without_losing_units():
    payload = build_stock_profile(
        "600519",
        profile_loader=loader(cninfo_profile()),
        market_cap_loader=loader(eastmoney_info(**{
            "总市值": "1.7万亿元",
            "流通市值": "8500亿元",
            "总股本": "12.5亿股",
            "流通股": "10亿股",
        })),
        now=NOW,
    )

    assert payload["total_market_cap"] == pytest.approx(1_700_000_000_000)
    assert payload["float_market_cap"] == pytest.approx(850_000_000_000)
    assert payload["total_shares"] == pytest.approx(1_250_000_000)
    assert payload["float_shares"] == pytest.approx(1_000_000_000)


def test_tencent_market_cap_fallback_parses_yuan_and_shares():
    from app.collectors.stock_profile_collector import (
        TENCENT_SOURCE,
        _parse_tencent_market_cap,
        load_market_cap,
    )

    fields = [""] * 77
    fields[1] = "贵州茅台"
    fields[2] = "600519"
    fields[44] = "16875.85"
    fields[45] = "16875.85"
    fields[72] = "1250081601"
    fields[73] = "1256197800"
    fields[76] = "1250081601"
    payload = 'v_sh600519="' + "~".join(fields) + '";'
    parsed = _parse_tencent_market_cap(payload, "600519")
    assert parsed["总市值"] == pytest.approx(1_687_585_000_000)
    assert parsed["流通市值"] == pytest.approx(1_687_585_000_000)
    assert parsed["总股本"] == pytest.approx(1_256_197_800)
    assert parsed["流通股"] == pytest.approx(1_250_081_601)
    assert parsed["__source"] == TENCENT_SOURCE

    def eastmoney_failure(symbol):
        raise RuntimeError("eastmoney unavailable")

    def tencent_success(symbol):
        return parsed

    merged = load_market_cap(
        "600519",
        eastmoney_loader=eastmoney_failure,
        tencent_loader=tencent_success,
    )
    assert merged["总市值"] == parsed["总市值"]
    assert merged["__source"] == TENCENT_SOURCE


def test_tencent_parse_rejects_a_different_stock():
    from app.collectors.stock_profile_collector import _parse_tencent_market_cap

    fields = [""] * 77
    fields[1] = "平安银行"
    fields[2] = "000001"
    fields[44] = "2192.84"
    fields[45] = "2192.87"
    with pytest.raises(ValueError, match="SymbolMismatch"):
        _parse_tencent_market_cap(
            'v_sz000001="' + "~".join(fields) + '";', "600519",
        )


def test_market_cap_fallback_uses_tencent_source_and_keeps_status_ok():
    from app.collectors.stock_profile_collector import TENCENT_SOURCE

    tencent = {
        "股票代码": "600519",
        "股票简称": "贵州茅台",
        "总市值": 1_687_585_000_000,
        "流通市值": 1_687_585_000_000,
        "总股本": 1_256_197_800,
        "流通股": 1_250_081_601,
        "__source": TENCENT_SOURCE,
    }
    payload = build_stock_profile(
        "600519",
        profile_loader=loader(cninfo_profile()),
        market_cap_loader=loader(tencent),
        now=NOW,
    )

    assert payload["status"] == "ok"
    assert payload["sources"]["market_cap"] == TENCENT_SOURCE
    assert payload["total_market_cap"] == pytest.approx(1_687_585_000_000)
    assert payload["total_shares"] == pytest.approx(1_256_197_800)
    assert payload["data_missing"] == []

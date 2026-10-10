import type { DigestGroup, DigestStock, ReviewDigest } from "../src/reviewDigestTypes.ts";

export function digestStock(patch: Partial<DigestStock> = {}): DigestStock {
  return {
    symbol: "600001", name: "测试首板", first_board_date: "2026-09-30", observed_days: 3,
    return_pct: 12.3, first_close: 10, cutoff_close: 11.23,
    position_label: "低位启动", industry: "测试行业", concepts: ["测试题材"],
    float_market_cap: 4_200_000_000, first_limit_time: "09:45", break_count: 0, turnover_rate: 8.2,
    max_board_height: null, second_board_date: null, second_open_pct: null,
    second_limit_time: null, second_break_count: null, second_turnover_rate: null, second_board_shape: null,
    data_missing: [], ...patch,
  };
}

export function digestGroup(scope: DigestGroup["scope"]): DigestGroup {
  return {
    scope, sample_size: 10, summary: "这一组的首板画像需要结合有效样本核对。",
    observations: [
      { id: `${scope}:position`, dimension: "position_label", text: "低位启动占本组有效位置的 5/8，基准全集为 12/25。", support_count: 5, sample_size: 8 },
      { id: `${scope}:seal`, dimension: "first_limit_time", text: "十点前封板占本组 6/9，观察期不同，暂不外推。", support_count: 6, sample_size: 9 },
      { id: `${scope}:unused`, dimension: "industry", text: "这条未被选择的说明不应成为默认结论。", support_count: 2, sample_size: 8 },
    ],
    selected_observation_ids: [`${scope}:seal`, `${scope}:position`],
    distributions: [{
      key: "position_label", label: "首板位置", valid_count: 8, total_count: 10,
      baseline_valid_count: 25, baseline_total_count: 50, note: "缺失位置不补成默认类别。",
      buckets: [
        { label: "低位启动", count: 5, share: 0.625, baseline_count: 12, baseline_share: 0.48 },
        { label: "其他位置", count: 3, share: 0.375, baseline_count: 13, baseline_share: 0.52 },
      ],
    }],
    stocks: [digestStock()], notes: ["观察天数可能不同。", "首板位置缺失 2 个。"],
  };
}

export function reviewDigest(): ReviewDigest {
  const leaders = digestGroup("leaders");
  leaders.observations[0].text = "低位启动占高标组有效位置的 5/8。";
  leaders.distributions = leaders.distributions.map(item => ({ ...item, baseline_valid_count: null, baseline_total_count: null,
    buckets: item.buckets.map(bucket => ({ ...bucket, baseline_count: null, baseline_share: null })) }));
  leaders.observations.push(
    { id: "leaders:second-shape", dimension: "second_board_shape", text: "二板换手板 7/10，包含有开板记录的样本。", support_count: 7, sample_size: 10 },
    { id: "leaders:second-open", dimension: "second_open_pct", text: "二板高开 6/10，开盘涨幅按首板收盘计算。", support_count: 6, sample_size: 10 },
  );
  leaders.selected_observation_ids.push("leaders:second-shape", "leaders:second-open");
  leaders.stocks = [digestStock({
    max_board_height: 4, second_board_date: "2026-10-08", second_open_pct: 3.6,
    second_limit_time: "09:36", second_break_count: 0, second_turnover_rate: 14.2, second_board_shape: "换手板",
  })];
  const weak = digestGroup("weak");
  weak.stocks = [digestStock({ symbol: "600002", name: "较差测试", return_pct: -8.5 })];
  return {
    version: "four-part-v1", as_of_date: "2026-10-09",
    candidate_dates: ["2026-09-25", "2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08"],
    market_dates: ["2026-09-28", "2026-09-29", "2026-09-30", "2026-10-08", "2026-10-09"],
    overview: {
      headline: "本期有 3 个可比交易日，其中 2 日候选晋级比例领先同期首板。",
      candidate_count: 50, excellent_count: 10, weak_count: 10, ordinary_count: 26, unobserved_count: 4,
      comparable_days: 3, outperform_days: 2, best_date: "2026-09-30",
      candidate_promoted: 8, candidate_promotion_total: 40, candidate_promotion_rate: 0.2,
      market_promoted: 10, market_promotion_total: 100, market_promotion_rate: 0.1, advantage_pp: 10,
    },
    excellent: digestGroup("excellent"), weak, leaders, notes: ["本摘要的 50 个候选来自截止日前五个真实交易日。"],
  };
}

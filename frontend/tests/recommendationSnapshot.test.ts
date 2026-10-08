import assert from "node:assert/strict";
import test from "node:test";

import { formatSnapshotTime, recommendationSnapshotView } from "../src/recommendationSnapshot.ts";

type Snapshot = Parameters<typeof recommendationSnapshotView>[0];

function snapshot(overrides: Partial<Snapshot> = {}): Snapshot {
  return {
    stage: "draft",
    target_trade_date: "2026-10-08",
    relay_base_date: "2026-09-30",
    refreshed_at: "2026-10-08T09:20:00+08:00",
    finalized_at: null,
    warnings: [],
    ...overrides,
  };
}

test("draft cutoff is 09:30 Shanghai inclusive, regardless of the browser timezone", () => {
  const previous = process.env.TZ;
  process.env.TZ = "America/Los_Angeles";
  try {
    const before = recommendationSnapshotView(snapshot(), new Date("2026-10-08T01:29:59Z"));
    const atOpen = recommendationSnapshotView(snapshot(), new Date("2026-10-08T01:30:00Z"));
    assert.equal(before.draftExpired, false);
    assert.equal(before.historical, false);
    assert.equal(before.title, "盘前候选草稿");
    assert.equal(atOpen.draftExpired, true);
    assert.equal(atOpen.title, "已过期盘前草稿");
    assert.ok(atOpen.notices.some((message) => message.includes("09:30")));
  } finally {
    if (previous === undefined) delete process.env.TZ;
    else process.env.TZ = previous;
  }
});

test("a previous-day final becomes history at Shanghai midnight, not UTC midnight", () => {
  const record = snapshot({ stage: "final", target_trade_date: "2026-10-07" });
  const before = recommendationSnapshotView(record, new Date("2026-10-07T15:59:59Z"));
  const midnight = recommendationSnapshotView(record, new Date("2026-10-07T16:00:00Z"));
  assert.equal(before.historical, false);
  assert.equal(midnight.historical, true);
  assert.equal(midnight.title, "历史固化候选");
  assert.equal(midnight.draftExpired, false);
  assert.ok(midnight.notices.some((message) => message.includes("2026-10-07")));
});

test("today's final remains a valid review snapshot after the market opens", () => {
  const view = recommendationSnapshotView(snapshot({
    stage: "final", finalized_at: "2026-10-08T09:25:00+08:00",
  }), new Date("2026-10-08T15:00:00+08:00"));
  assert.equal(view.historical, false);
  assert.equal(view.draftExpired, false);
  assert.equal(view.title, "盘前固化候选");
  assert.deepEqual(view.notices, []);
  assert.equal(view.finalizedLabel, "2026-10-08 09:25:00（上海时间）");
});

test("an old draft is expired, but a confirmed future target is not expired by today's open", () => {
  const now = new Date("2026-10-08T10:00:00+08:00");
  const old = recommendationSnapshotView(snapshot({ target_trade_date: "2026-09-30" }), now);
  const future = recommendationSnapshotView(snapshot({ target_trade_date: "2026-10-09" }), now);
  assert.equal(old.historical, true);
  assert.equal(old.draftExpired, true);
  assert.equal(old.title, "历史候选快照");
  assert.equal(future.historical, false);
  assert.equal(future.draftExpired, false);
});

test("fallback names the current missed target separately from the historical candidate cohort", () => {
  const record = snapshot({
    stage: "final", target_trade_date: "2026-09-29", relay_base_date: "2026-09-28",
    refreshed_at: "2026-09-29T09:20:00+08:00", finalized_at: "2026-09-29T09:25:00+08:00",
    warnings: ["历史快照资讯不完整"],
    display_context: {
      is_history_fallback: true, latest_target_trade_date: "2026-10-08",
      latest_stage: "missed_cutoff", latest_refreshed_at: "2026-10-08T10:00:00+08:00",
      latest_warnings: ["当前盘前固化已错过", "历史快照资讯不完整"],
    },
  });
  const original = structuredClone(record);
  const view = recommendationSnapshotView(record, new Date("2026-10-08T10:00:00+08:00"));
  assert.equal(view.title, "历史固化候选");
  assert.equal(view.targetLabel, "2026-09-29 目标日");
  assert.equal(view.baseLabel, "2026-09-28");
  assert.equal(view.refreshedLabel, "2026-09-29 09:20:00（上海时间）");
  assert.ok(view.notices[0].includes("当前目标日 2026-10-08"));
  assert.ok(view.notices[0].includes("未在开盘前固化"));
  assert.ok(view.notices[0].includes("2026-09-29 目标日"));
  assert.ok(view.notices[1].includes("2026-10-08 10:00:00"));
  assert.deepEqual(view.warnings, ["当前盘前固化已错过", "历史快照资讯不完整"]);
  assert.deepEqual(record, original, "display decoration must not relabel stored provenance");
});

test("an unknown target stays unknown, including calendar failure and fallback context", () => {
  const view = recommendationSnapshotView(snapshot({
    target_trade_date: null, relay_base_date: null, warnings: ["交易日历读取失败"],
    display_context: {
      is_history_fallback: true, latest_target_trade_date: null,
      latest_stage: "draft", latest_refreshed_at: "2026-10-08T10:00:00+08:00",
    },
  }), new Date("2026-10-08T10:00:00+08:00"));
  assert.equal(view.targetLabel, "目标日未确认");
  assert.equal(view.baseLabel, "未确认");
  assert.equal(view.draftExpired, false);
  assert.ok(view.notices.some((message) => message.includes("当前目标日未确认")));
  assert.ok(view.notices.some((message) => message.includes("尚未固化")));
  assert.ok(view.notices.every((message) => !message.includes("下一交易日")));
  assert.deepEqual(view.warnings, ["交易日历读取失败"]);
});

test("an empty missed-cutoff response retains its target, refresh provenance, and warnings", () => {
  const view = recommendationSnapshotView(snapshot({
    stage: "missed_cutoff", warnings: ["目标日缺少正式盘前快照"],
  }), new Date("2026-10-08T10:00:00+08:00"));
  assert.equal(view.targetLabel, "2026-10-08 目标日");
  assert.equal(view.title, "未固化候选快照");
  assert.ok(view.notices.some((message) => message.includes("未在开盘前固化")));
  assert.deepEqual(view.warnings, ["目标日缺少正式盘前快照"]);
});

test("legacy responses without display_context remain supported", () => {
  const view = recommendationSnapshotView(snapshot(), new Date("2026-10-08T01:00:00Z"));
  assert.equal(view.title, "盘前候选草稿");
  assert.deepEqual(view.warnings, []);
  assert.deepEqual(view.notices, []);
});

test("snapshot timestamps include the full Shanghai date and preserve missing timestamps", () => {
  assert.equal(formatSnapshotTime("2026-10-07T17:20:30Z"), "2026-10-08 01:20:30（上海时间）");
  assert.equal(formatSnapshotTime("2026-10-08T09:20:30"), "2026-10-08 09:20:30（上海时间）");
  assert.equal(formatSnapshotTime(null), "时间未确认");
  assert.equal(formatSnapshotTime("invalid"), "时间未确认");
});

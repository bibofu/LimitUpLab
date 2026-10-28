import assert from "node:assert/strict";
import test from "node:test";

import {
  barWidth,
  formatCount,
  formatDuration,
  formatLatency,
  formatPercent,
  formatUsd,
} from "../src/utils/observability.ts";

test("observability formatters keep unknown values explicit", () => {
  assert.equal(formatCount(null), "—");
  assert.equal(formatPercent(null), "—");
  assert.equal(formatUsd(null), "—");
  assert.equal(formatLatency(null), "—");
  assert.equal(formatDuration(null), "—");
});

test("ratios are shown as percentages and latency picks a readable unit", () => {
  assert.equal(formatPercent(0.5), "50.0%");
  assert.equal(formatPercent(0.1234, 2), "12.34%");
  assert.equal(formatLatency(850), "850ms");
  assert.equal(formatLatency(1500), "1.50s");
  assert.equal(formatDuration(90), "2分钟");
  assert.equal(formatDuration(7200), "2.0小时");
});

test("usd and trend bars stay bounded", () => {
  assert.equal(formatUsd(0), "$0");
  assert.equal(formatUsd(0.002345), "$0.0023");
  assert.equal(formatUsd(1.5), "$1.50");
  assert.equal(barWidth(5, 10), 50);
  assert.equal(barWidth(20, 10), 100);
  assert.equal(barWidth(-1, 10), 0);
  assert.equal(barWidth(3, 0), 0);
});

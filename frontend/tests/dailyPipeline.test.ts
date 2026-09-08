import assert from "node:assert/strict";
import test from "node:test";
import { dailyPipelineNotice, DAILY_COLLECTION_SCHEDULE } from "../src/dailyPipeline.ts";
import type { MarketCollectionStatus } from "../src/types.ts";

const pipeline = (phase: string, status: string) => ({
  trade_date: "2026-09-08", status, phase,
} as MarketCollectionStatus);

test("preview success is still explicitly awaiting final verification", () => {
  assert.match(dailyPipelineNotice(pipeline("preview", "success"), "2026-09-08")!, /16:10.*尚未齐全/);
  assert.match(DAILY_COLLECTION_SCHEDULE, /15:30.*16:10/);
});

test("final running and incomplete states cannot appear fully verified", () => {
  assert.match(dailyPipelineNotice(pipeline("final", "running"), "2026-09-08")!, /正在/);
  for (const status of ["partial", "error"]) {
    assert.match(dailyPipelineNotice(pipeline("final", status), "2026-09-08")!, /尚未全部通过/);
  }
  assert.equal(dailyPipelineNotice(pipeline("final", "success"), "2026-09-08"), null);
});

test("absent or differently dated runs are not attached to displayed data", () => {
  assert.equal(dailyPipelineNotice(null, "2026-09-08"), null);
  assert.equal(dailyPipelineNotice(pipeline("preview", "success"), "2026-09-07"), null);
});

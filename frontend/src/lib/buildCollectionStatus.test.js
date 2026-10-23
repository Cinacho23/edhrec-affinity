import assert from "node:assert/strict";
import test from "node:test";

import { formatCollectionDuration, getBuildCollectorStatus } from "./buildCollectionStatus.js";

const NOW = Date.parse("2026-10-23T03:00:00Z");
const progress = () => ({ updated_at: "2026-10-23T02:47:17Z", blocked: false,
  capacity: { remaining_requests: 12000000, configured_requests_per_second: 40,
    effective_requests_per_second: 1.5, rate_limit_learned: true, inventory_complete: true,
    estimated_minimum_seconds: 8000000, measured_projection_seconds: 9000000,
    observed_requests_per_second: 1.333, projected_finish_at: "2027-02-04T06:47:17Z", status: "collecting" },
  rate_control: { learned_rate: 1.5, effective_requests_per_second: 1.5, status: "learned_below_observed_throttle" },
});

test("configured maximum remains distinct from a provisional learned operating rate", () => {
  const result = getBuildCollectorStatus(progress(), NOW);
  assert.equal(result.label, "Learned operating ceiling");
  assert.equal(result.operatingRate, 1.5);
  assert.equal(result.configuredRate, 40);
  assert.equal(result.minimumSeconds, 8000000);
  assert.equal(result.projectedSeconds, 9000000);
  const calibrating = progress();
  calibrating.rate_control = { learned_rate: null, status: "calibrating" };
  calibrating.capacity.rate_limit_learned = false;
  calibrating.capacity.effective_requests_per_second = 40;
  const result2 = getBuildCollectorStatus(calibrating, NOW);
  assert.equal(result2.label, "Calibrating request rate");
  assert.equal(result2.operatingRate, null);
  assert.equal(result2.minimumSeconds, null);
  assert.equal(result2.observedRate, 1.333);
});

test("known cooldowns retain conditional projections while indefinite pauses defer them", () => {
  const source = { ...progress(), blocked: true, blocked_until: "2026-10-23T03:47:17Z" };
  const result = getBuildCollectorStatus(source, NOW);
  assert.equal(result.label, "Collection paused");
  assert.equal(result.minimumSeconds, 8000000);
  assert.equal(result.projectedSeconds, 9000000);
  assert.equal(result.projectedFinishAt, source.capacity.projected_finish_at);
  assert.equal(result.blockedUntil, source.blocked_until);
  assert.equal(getBuildCollectorStatus(source, NOW + 3600000).blockedUntil, null);
  const indefinite = getBuildCollectorStatus({ ...progress(), resource_limit: "disk" }, NOW);
  assert.equal(indefinite.paused, true);
  assert.equal(indefinite.projectedSeconds, null);
  assert.equal(getBuildCollectorStatus({ ...progress(), blocked: true }, NOW).projectedFinishAt, null);
});

test("missing and invalid observations never imply a known rate or completion time", () => {
  for (const source of [null, {}, { ...progress(), updated_at: "invalid" },
    { ...progress(), updated_at: "2026-10-24T00:00:00Z" }, { ...progress(), capacity: null }]) {
    assert.equal(getBuildCollectorStatus(source, NOW), null);
  }
  const source = progress();
  source.rate_control.learned_rate = NaN;
  source.capacity.observed_requests_per_second = 0;
  source.capacity.measured_projection_seconds = null;
  source.capacity.remaining_requests = -1;
  const result = getBuildCollectorStatus(source, NOW);
  assert.equal(result.operatingRate, null);
  assert.equal(result.observedRate, null);
  assert.equal(result.projectedSeconds, null);
  assert.equal(result.remaining, null);
  assert.equal(getBuildCollectorStatus(progress(), NOW + 6 * 3600000).stale, true);
});

test("carried observations retain their own timestamp and unknown inventory never says complete", () => {
  const source = progress();
  source.capacity.observed_rate_at = "2026-10-22T22:00:00Z";
  source.capacity.remaining_requests = 0;
  source.capacity.inventory_complete = false;
  source.capacity.estimated_minimum_seconds = 0;
  source.capacity.measured_projection_seconds = 0;
  const result = getBuildCollectorStatus(source, NOW);
  assert.equal(result.observedRateAt, "2026-10-22T22:00:00Z");
  assert.equal(result.complete, false);
  assert.equal(result.minimumSeconds, null);
  assert.equal(result.projectedSeconds, null);
});

test("request-time formatting covers long backfills without false minute precision", () => {
  assert.equal(formatCollectionDuration(null), "Not available");
  assert.equal(formatCollectionDuration(NaN), "Not available");
  assert.equal(formatCollectionDuration(0), "Complete");
  assert.equal(formatCollectionDuration(1), "1 minute");
  assert.equal(formatCollectionDuration(3600), "1 hour");
  assert.equal(formatCollectionDuration(48 * 3600), "2 days");
  assert.equal(formatCollectionDuration(8000000), "92 days 15 hours");
});

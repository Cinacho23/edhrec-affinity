import assert from "node:assert/strict";
import test from "node:test";

import { formatCollectionDuration, getBuildCollectorExecutionStatus, getBuildCollectorStatus } from "./buildCollectionStatus.js";

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
  assert.equal(result.label, "Last published batch");
  assert.equal(result.operatingRate, 1.5);
  assert.equal(result.configuredRate, 40);
  assert.equal(result.minimumSeconds, 8000000);
  assert.equal(result.projectedSeconds, 9000000);
  const calibrating = progress();
  calibrating.rate_control = { learned_rate: null, status: "calibrating" };
  calibrating.capacity.rate_limit_learned = false;
  calibrating.capacity.effective_requests_per_second = 40;
  const result2 = getBuildCollectorStatus(calibrating, NOW);
  assert.equal(result2.label, "Last published batch");
  assert.equal(result2.operatingRate, null);
  assert.equal(result2.minimumSeconds, null);
  assert.equal(result2.observedRate, 1.333);
});

test("distributed status uses the combined ceiling and reports independent machines", () => {
  const source = progress();
  source.distributed = { runners: 16 };
  source.capacity.effective_requests_per_second = 16 * 1.318359375;
  source.rate_control = { learned_rate: 16 * 1.318359375,
    effective_requests_per_second: 16 * 1.318359375, status: "independent_workers" };
  const result = getBuildCollectorStatus(source, NOW);
  assert.equal(result.machines, 16);
  assert.equal(result.operatingRate, 21.09375);
  source.distributed.blocked_workers = 1;
  const partial = getBuildCollectorStatus(source, NOW);
  assert.equal(partial.blockedMachines, 1);
  assert.equal(partial.minimumSeconds, null);
  assert.equal(partial.projectedSeconds, null);
  assert.equal(partial.projectedFinishAt, null);
  for (const runners of [0, 1, 17, "16", null]) {
    source.distributed.runners = runners;
    assert.equal(getBuildCollectorStatus(source, NOW).machines, null);
  }
});

test("known cooldowns retain conditional projections while indefinite pauses defer them", () => {
  const source = { ...progress(), blocked: true, blocked_until: "2026-10-23T03:47:17Z" };
  const result = getBuildCollectorStatus(source, NOW);
  assert.equal(result.label, "Last published batch: source cooldown");
  assert.equal(result.minimumSeconds, 8000000);
  assert.equal(result.projectedSeconds, 9000000);
  assert.equal(result.projectedFinishAt, source.capacity.projected_finish_at);
  assert.equal(result.blockedUntil, source.blocked_until);
  assert.equal(getBuildCollectorStatus(source, NOW + 3600000).blockedUntil, null);
  assert.equal(getBuildCollectorStatus(source, NOW + 3600000).label, "Last published batch stopped");
  const indefinite = getBuildCollectorStatus({ ...progress(), resource_limit: "disk" }, NOW);
  assert.equal(indefinite.paused, true);
  assert.equal(indefinite.projectedSeconds, null);
  assert.equal(getBuildCollectorStatus({ ...progress(), blocked: true }, NOW).projectedFinishAt, null);
});

test("older checkpoints recover a recorded cooldown from its dated remaining duration", () => {
  const source = { ...progress(), blocked: true, blocked_until: null, updated_at: "2026-10-23T03:15:23Z" };
  source.capacity.evaluated_at = "2026-10-23T03:15:23Z";
  source.capacity.cooldown_seconds = 3599.858;
  const now = Date.parse("2026-10-23T03:16:00Z");
  const result = getBuildCollectorStatus(source, now);
  assert.equal(result.blockedUntil, "2026-10-23T04:15:22.858Z");
  assert.equal(result.projectedSeconds, source.capacity.measured_projection_seconds);
  assert.equal(result.hasRecordedCooldown, true);
  delete source.capacity.evaluated_at;
  assert.equal(getBuildCollectorStatus(source, now).blockedUntil, result.blockedUntil);
  source.blocked_until = "2026-10-23T05:00:00Z";
  assert.equal(getBuildCollectorStatus(source, now).blockedUntil, source.blocked_until);
  source.blocked_until = null;
  for (const seconds of [null, 0, -1, NaN, Infinity, "3600"]) {
    source.capacity.cooldown_seconds = seconds;
    const indefinite = getBuildCollectorStatus(source, now);
    assert.equal(indefinite.hasRecordedCooldown, false);
    assert.equal(indefinite.projectedSeconds, null);
  }
  source.capacity.cooldown_seconds = 3600;
  source.updated_at = "invalid";
  assert.equal(getBuildCollectorStatus(source, now), null);
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

test("fresh execution status supersedes an older published pause without asserting request progress", () => {
  const now = Date.parse("2026-10-23T11:30:00Z");
  const execution = { runId: 39936571861, status: "in_progress", checkedAt: "2026-10-23T11:30:00Z",
    createdAt: "2026-10-23T10:05:00Z", startedAt: "2026-10-23T10:06:00Z" };
  const checkpoint = "2026-10-23T04:25:00Z";
  const active = getBuildCollectorExecutionStatus(execution, checkpoint, now);
  assert.equal(active.label, "Collector running");
  assert.equal(active.running, true);
  assert.equal(active.activityAt, "2026-10-23T10:06:00.000Z");
  assert.equal(active.url, "https://github.com/Cinacho23/edhrec-affinity/actions/runs/39936571861");
  assert.equal(getBuildCollectorExecutionStatus({ ...execution, status: "queued" }, checkpoint, now).label, "Collector queued");
  assert.equal(getBuildCollectorExecutionStatus({ ...execution, status: "completed", conclusion: "success" }, checkpoint, now).label, "Latest collector run finished");
  assert.equal(getBuildCollectorExecutionStatus({ ...execution, status: "completed", conclusion: "failure" }, checkpoint, now).label, "Latest collector run stopped");
  for (const invalid of [null, { ...execution, status: "unknown" }, { ...execution, runId: -1 },
    { ...execution, checkedAt: "invalid" }, { ...execution, checkedAt: "2026-10-23T11:20:00Z" }]) {
    assert.equal(getBuildCollectorExecutionStatus(invalid, checkpoint, now), null);
  }
  assert.equal(getBuildCollectorExecutionStatus(execution, "2026-10-23T11:00:00Z", now), null);
});

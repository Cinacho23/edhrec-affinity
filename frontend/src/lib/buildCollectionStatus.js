const FUTURE_TOLERANCE_MS = 5 * 60 * 1000;
const SNAPSHOT_MAX_AGE_MS = 6 * 60 * 60 * 1000;

function nonnegative(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

function positive(value) {
  return nonnegative(value) > 0 ? value : null;
}

export function formatCollectionDuration(seconds) {
  if (nonnegative(seconds) === null) return "Not available";
  if (seconds === 0) return "Complete";
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) return `${minutes} ${minutes === 1 ? "minute" : "minutes"}`;
  const hours = Math.ceil(minutes / 60);
  if (hours < 48) return `${hours} ${hours === 1 ? "hour" : "hours"}`;
  const days = Math.floor(hours / 24);
  const remainder = hours % 24;
  return `${days} days${remainder ? ` ${remainder} hours` : ""}`;
}

export function getBuildCollectorStatus(progress, now = Date.now()) {
  const updated = typeof progress?.updated_at === "string" ? Date.parse(progress.updated_at) : NaN;
  if (!Number.isFinite(updated) || !Number.isFinite(now) || updated > now + FUTURE_TOLERANCE_MS
      || !progress?.capacity || typeof progress.capacity !== "object") return null;
  const capacity = progress.capacity;
  const control = progress.rate_control;
  const learnedRate = positive(control?.learned_rate);
  const learned = learnedRate !== null && (capacity.rate_limit_learned === true
    || control?.status === "learned_below_observed_throttle");
  const operatingRate = learned ? positive(control?.effective_requests_per_second)
    ?? positive(capacity.effective_requests_per_second) ?? learnedRate : null;
  const configuredRate = positive(capacity.configured_requests_per_second);
  const observedRate = positive(capacity.observed_requests_per_second);
  const remaining = Number.isSafeInteger(capacity.remaining_requests) && capacity.remaining_requests >= 0
    ? capacity.remaining_requests : null;
  const paused = progress.blocked === true || Boolean(progress.resource_limit);
  const complete = remaining === 0 && capacity.status === "complete";
  const stale = now - updated >= SNAPSHOT_MAX_AGE_MS;
  let blockedUntil = typeof progress.blocked_until === "string" ? progress.blocked_until : null;
  let cooldownAt = blockedUntil === null ? NaN : Date.parse(blockedUntil);
  // Older checkpoints retained the remaining cooldown in capacity before
  // copying its end time into progress. Recover that recorded pause only when
  // both the duration and its observation time are known.
  if (!Number.isFinite(cooldownAt) && progress.blocked === true && positive(capacity.cooldown_seconds) !== null) {
    const evaluated = typeof capacity.evaluated_at === "string" ? Date.parse(capacity.evaluated_at) : NaN;
    const observed = Number.isFinite(evaluated) && evaluated <= now + FUTURE_TOLERANCE_MS ? evaluated : updated;
    const inferred = new Date(observed + capacity.cooldown_seconds * 1000);
    if (Number.isFinite(inferred.getTime())) {
      cooldownAt = inferred.getTime();
      blockedUntil = inferred.toISOString();
    }
  }
  const hasRecordedCooldown = Number.isFinite(cooldownAt) && !progress.resource_limit;
  const canProject = !paused || hasRecordedCooldown;
  const observedAt = capacity.observed_rate_at ?? control?.observed_rate_at;
  const observedTimestamp = typeof observedAt === "string" ? Date.parse(observedAt) : NaN;
  return {
    updatedAt: progress.updated_at, stale, paused, complete,
    label: complete ? "Last published collection complete" : paused && Number.isFinite(cooldownAt) && cooldownAt > now
      ? "Last published batch: source cooldown" : paused ? "Last published batch stopped" : "Last published batch",
    learned, operatingRate, configuredRate, observedRate, remaining,
    observedRateAt: Number.isFinite(observedTimestamp) && observedTimestamp <= now + FUTURE_TOLERANCE_MS ? observedAt : null,
    minimumSeconds: learned && (remaining !== 0 || complete) ? nonnegative(capacity.estimated_minimum_seconds) : null,
    projectedSeconds: canProject && (remaining !== 0 || complete) ? nonnegative(capacity.measured_projection_seconds) : null,
    projectedFinishAt: canProject && typeof capacity.projected_finish_at === "string"
      && Number.isFinite(Date.parse(capacity.projected_finish_at)) ? capacity.projected_finish_at : null,
    blockedUntil: Number.isFinite(cooldownAt) && cooldownAt > now ? blockedUntil : null,
    hasRecordedCooldown,
    lastThrottleAt: typeof control?.last_throttle?.at === "string" ? control.last_throttle.at : null,
    inventoryComplete: capacity.inventory_complete === true,
  };
}

export function getBuildCollectorExecutionStatus(execution, checkpointAt, now = Date.now()) {
  const checked = typeof execution?.checkedAt === "string" ? Date.parse(execution.checkedAt) : NaN;
  const created = typeof execution?.createdAt === "string" ? Date.parse(execution.createdAt) : NaN;
  const started = typeof execution?.startedAt === "string" ? Date.parse(execution.startedAt) : NaN;
  const checkpoint = typeof checkpointAt === "string" ? Date.parse(checkpointAt) : NaN;
  if (!Number.isFinite(now) || !Number.isFinite(checked) || checked > now + FUTURE_TOLERANCE_MS
      || now - checked >= 5 * 60 * 1000 || !Number.isFinite(created) || created > now + FUTURE_TOLERANCE_MS
      || !Number.isSafeInteger(execution?.runId) || execution.runId <= 0) return null;
  const activityAt = Number.isFinite(started) && started <= now + FUTURE_TOLERANCE_MS ? started : created;
  if (Number.isFinite(checkpoint) && activityAt <= checkpoint) return null;
  const running = execution.status === "in_progress";
  const queued = ["queued", "requested", "waiting", "pending"].includes(execution.status);
  if (!running && !queued && execution.status !== "completed") return null;
  return {
    label: running ? "Collector running" : queued ? "Collector queued" : execution.conclusion === "success"
      ? "Latest collector run finished" : "Latest collector run stopped",
    running, queued, completed: execution.status === "completed", checkedAt: execution.checkedAt,
    activityAt: new Date(activityAt).toISOString(),
    url: `https://github.com/Cinacho23/edhrec-affinity/actions/runs/${execution.runId}`,
  };
}

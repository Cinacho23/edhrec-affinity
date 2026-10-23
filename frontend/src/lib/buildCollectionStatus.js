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
  const cooldownAt = typeof progress.blocked_until === "string" ? Date.parse(progress.blocked_until) : NaN;
  const hasRecordedCooldown = Number.isFinite(cooldownAt) && !progress.resource_limit;
  const canProject = !paused || hasRecordedCooldown;
  const observedAt = capacity.observed_rate_at ?? control?.observed_rate_at;
  const observedTimestamp = typeof observedAt === "string" ? Date.parse(observedAt) : NaN;
  return {
    updatedAt: progress.updated_at, stale, paused, complete,
    label: complete ? "Collection complete" : paused ? "Collection paused" : learned ? "Learned operating ceiling" : "Calibrating request rate",
    learned, operatingRate, configuredRate, observedRate, remaining,
    observedRateAt: Number.isFinite(observedTimestamp) && observedTimestamp <= now + FUTURE_TOLERANCE_MS ? observedAt : null,
    minimumSeconds: learned && (remaining !== 0 || complete) ? nonnegative(capacity.estimated_minimum_seconds) : null,
    projectedSeconds: canProject && (remaining !== 0 || complete) ? nonnegative(capacity.measured_projection_seconds) : null,
    projectedFinishAt: canProject && typeof capacity.projected_finish_at === "string"
      && Number.isFinite(Date.parse(capacity.projected_finish_at)) ? capacity.projected_finish_at : null,
    blockedUntil: Number.isFinite(cooldownAt) && cooldownAt > now ? progress.blocked_until : null,
    hasRecordedCooldown,
    lastThrottleAt: typeof control?.last_throttle?.at === "string" ? control.last_throttle.at : null,
    inventoryComplete: capacity.inventory_complete === true,
  };
}

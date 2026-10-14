// Backend connectivity: failure classification + a latest-wins tracker.
//
// A single data endpoint (e.g. `/simulations/jobs`) is NOT a reliable proxy
// for backend health, and responses can settle out of order when requests
// overlap. This module isolates that decision so it can be reasoned about and
// unit-tested without React, axios or a running server.
//
// Rules encoded here:
//   * a server response of ANY status  => the backend is reachable;
//   * no response at all (refused, DNS, timeout) => unreachable, but that
//     alone must not condemn the backend — the caller confirms with the
//     dedicated health endpoint before going offline;
//   * an older, slower result must never override a newer one (sequencing);
//   * a deliberately cancelled request carries no information.

/** How a finished request speaks to backend reachability. */
export type Reachability = 'reachable' | 'unreachable' | 'canceled'

interface ErrorLike {
  code?: string
  response?: unknown
}

const asErrorLike = (err: unknown): ErrorLike =>
  typeof err === 'object' && err !== null ? (err as ErrorLike) : {}

/**
 * Classify a thrown request error:
 *   - `ERR_CANCELED`            -> 'canceled'    (we aborted; say nothing)
 *   - an HTTP `response` exists -> 'reachable'   (server answered, any status)
 *   - otherwise                 -> 'unreachable' (no bytes came back)
 */
export const classifyRequestFailure = (err: unknown): Reachability => {
  const error = asErrorLike(err)
  if (error.code === 'ERR_CANCELED') return 'canceled'
  if (error.response !== undefined) return 'reachable'
  return 'unreachable'
}

/**
 * Human-readable message for a failed data request. `subject` names the
 * request (e.g. "Could not load simulation jobs"); a server error keeps its
 * HTTP status and backend `detail` so it stays actionable.
 */
export const describeRequestFailure = (
  err: unknown,
  subject: string,
): string => {
  const error = asErrorLike(err) as {
    response?: { status?: number; data?: { detail?: string } }
  }
  const status = error.response?.status
  if (status !== undefined) {
    const detail = error.response?.data?.detail
    return detail
      ? `${subject} (HTTP ${status}: ${detail})`
      : `${subject} (HTTP ${status}).`
  }
  return 'Backend unreachable — start the API server (uvicorn app.main:app).'
}

/**
 * Tracks backend reachability with latest-wins semantics: only the most
 * recently started probe may change the state, so a slow failure can never
 * overwrite a newer success. Starts optimistic (`online = true`), matching the
 * UI's initial assumption until a real request contradicts it.
 */
export class BackendHealthTracker {
  private counter = 0
  private online = true

  /** Register the start of a probe and return its token. */
  begin(): number {
    this.counter += 1
    return this.counter
  }

  /** True while `seq` is still the newest probe. */
  isCurrent(seq: number): boolean {
    return seq === this.counter
  }

  /** Fold a finished probe in; returns the connectivity to display. */
  settle(seq: number, result: Reachability): boolean {
    if (!this.isCurrent(seq)) return this.online
    if (result === 'reachable') this.online = true
    else if (result === 'unreachable') this.online = false
    // 'canceled' carries no information: leave the state unchanged.
    return this.online
  }

  get isOnline(): boolean {
    return this.online
  }

  /**
   * Fold a *data* request (e.g. `/simulations/jobs`) into connectivity.
   *
   * - A response from the server means the backend is reachable.
   * - A cancelled request says nothing.
   * - No response at all is ambiguous — that one route may be broken or slow —
   *   so `probeHealth` (the dedicated `/system/status` endpoint) decides.
   *
   * Stale data requests are ignored up-front, so a slow failure neither
   * overrides a newer success nor even triggers a health probe.
   */
  async settleDataRequest(
    seq: number,
    result: Reachability,
    probeHealth: () => Promise<Reachability>,
  ): Promise<boolean> {
    if (result !== 'unreachable') return this.settle(seq, result)
    if (!this.isCurrent(seq)) return this.online
    const healthSeq = this.begin()
    const health = await probeHealth()
    return this.settle(healthSeq, health)
  }
}

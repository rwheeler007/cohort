/**
 * Cohort HTTP client for the channel plugin.
 *
 * All calls target the Cohort Starlette API on localhost.
 * Respond calls retry aggressively (critical path).
 * Heartbeat is best-effort (no retry).
 */

import type { PollResponse, ClaimResponse, ChannelConfig } from "./types.js";

export class CohortClient {
  private baseUrl: string;
  private sessionId: string;

  constructor(config: ChannelConfig) {
    this.baseUrl = config.cohort_base_url;
    this.sessionId = config.session_id;
  }

  /**
   * Poll for the next pending agent request. No side effects.
   */
  async poll(): Promise<PollResponse> {
    const res = await fetch(`${this.baseUrl}/api/channel/poll`);
    if (!res.ok) {
      throw new Error(`Poll failed: ${res.status} ${res.statusText}`);
    }
    return (await res.json()) as PollResponse;
  }

  /**
   * Claim a request, get the full prompt.
   */
  async claim(requestId: string): Promise<ClaimResponse> {
    const res = await fetch(
      `${this.baseUrl}/api/channel/${requestId}/claim`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ session_id: this.sessionId }),
      }
    );
    if (!res.ok) {
      const body = await res.text();
      throw new Error(`Claim failed (${res.status}): ${body}`);
    }
    return (await res.json()) as ClaimResponse;
  }

  /**
   * Deliver a response. Retries aggressively since this is critical.
   */
  async respond(
    requestId: string,
    content: string,
    metadata?: Record<string, unknown>
  ): Promise<void> {
    let lastError: Error | null = null;
    for (let attempt = 0; attempt < 5; attempt++) {
      try {
        const body: Record<string, unknown> = { content };
        if (metadata) body.metadata = metadata;
        const res = await fetch(
          `${this.baseUrl}/api/channel/${requestId}/respond`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }
        );
        if (!res.ok) {
          const body = await res.text();
          throw new Error(`Respond failed (${res.status}): ${body}`);
        }
        return;
      } catch (e) {
        lastError = e as Error;
        if (attempt < 4) {
          await new Promise((r) => setTimeout(r, 2000 * (attempt + 1)));
        }
      }
    }
    throw lastError!;
  }

  /**
   * Report request error. Best-effort with one retry.
   */
  async error(requestId: string, errorMsg: string): Promise<void> {
    for (let attempt = 0; attempt < 2; attempt++) {
      try {
        await fetch(
          `${this.baseUrl}/api/channel/${requestId}/error`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ error: errorMsg }),
          }
        );
        return;
      } catch {
        if (attempt === 0) {
          await new Promise((r) => setTimeout(r, 1000));
        }
      }
    }
  }

  /**
   * Send heartbeat. Best-effort.
   */
  async heartbeat(): Promise<void> {
    try {
      await fetch(`${this.baseUrl}/api/channel/heartbeat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: this.sessionId,
          pid: process.pid,
        }),
      });
    } catch {
      // Best-effort
    }
  }
}

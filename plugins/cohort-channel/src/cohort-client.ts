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
  private channelId: string | undefined;

  constructor(config: ChannelConfig) {
    this.baseUrl = config.cohort_base_url;
    this.sessionId = config.session_id;
    this.channelId = config.channel_id;
  }

  /**
   * Poll for the next pending agent request. No side effects.
   * If channelId is set, only polls for that channel's requests.
   */
  async poll(): Promise<PollResponse> {
    const url = this.channelId
      ? `${this.baseUrl}/api/channel/poll?channel_id=${encodeURIComponent(this.channelId)}`
      : `${this.baseUrl}/api/channel/poll`;
    const res = await fetch(url);
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
   * Post a message to a Cohort channel as a specific agent.
   * Used for multi-round roundtable responses where Claude drives
   * the discussion and posts each agent's contribution independently.
   */
  async postMessage(
    channel: string,
    sender: string,
    message: string,
    threadId?: string,
  ): Promise<{ success: boolean; message_id?: string }> {
    const body: Record<string, unknown> = { channel, sender, message };
    if (threadId) body.thread_id = threadId;

    const res = await fetch(`${this.baseUrl}/api/send`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`Post failed (${res.status}): ${text}`);
    }
    return (await res.json()) as { success: boolean; message_id?: string };
  }

  /**
   * Send heartbeat. Best-effort.
   */
  async heartbeat(): Promise<void> {
    try {
      const body: Record<string, unknown> = {
        session_id: this.sessionId,
        pid: process.pid,
      };
      if (this.channelId) body.channel_id = this.channelId;

      await fetch(`${this.baseUrl}/api/channel/heartbeat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
    } catch {
      // Best-effort
    }
  }
}

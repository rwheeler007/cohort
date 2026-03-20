/**
 * Cohort Channel Plugin -- shared types.
 */

export interface RequestMeta {
  id: string;
  agent_id: string;
  channel_id: string;
  response_mode: string;
}

export interface ClaimResponse {
  id: string;
  prompt: string;
  agent_id: string;
  channel_id: string;
  thread_id: string | null;
  response_mode: string;
  metadata: Record<string, unknown>;
}

export interface PollResponse {
  request: RequestMeta | null;
  reason?: string;
}

export interface ChannelConfig {
  cohort_base_url: string;
  poll_interval_ms: number;
  heartbeat_interval_ms: number;
  session_id: string;
  channel_id?: string;
}

export interface RegisterResponse {
  ok: boolean;
  channel_id?: string;
  session_id?: string;
  warn?: boolean;
  error?: string;
  limit?: number;
  active?: number;
}

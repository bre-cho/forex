export type BrainStageDecision = {
  stage: string;
  action: string;
  reason: string;
  score: number;
  latency_ms?: number;
  payload?: Record<string, unknown>;
};

export type BrainCycleResult = {
  cycle_id: string;
  action: string;
  reason: string;
  final_score: number;
  selected_signal?: Record<string, unknown> | null;
  execution_intent?: Record<string, unknown> | null;
  stage_decisions: BrainStageDecision[];
  policy_snapshot: Record<string, unknown>;
  created_at: number;
};

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

async function jsonFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`API lỗi ${res.status}: ${await res.text()}`);
  return res.json() as Promise<T>;
}

export function getTradingBrainHealth() {
  return jsonFetch<Record<string, unknown>>("/api/trading-brain/health");
}

export function previewTradingBrainCycle(payload?: Record<string, unknown>) {
  return jsonFetch<BrainCycleResult>("/api/trading-brain/preview-cycle", {
    method: "POST",
    body: JSON.stringify(payload ?? {}),
  });
}

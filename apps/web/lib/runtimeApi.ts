import { api } from './api';

export interface RuntimeStatus {
  bot_instance_id: string;
  status: string;
  started_at?: number;
  error_message?: string;
  equity?: number;
  open_trades?: number;
  total_trades?: number;
  broker_health?: Record<string, unknown>;
  last_brain_cycle?: Record<string, unknown>;
  metadata?: Record<string, unknown>;
}

export interface DailyState {
  bot_instance_id: string;
  locked: boolean;
  lock_reason?: string | null;
  daily_profit_amount?: number;
  daily_loss_pct?: number;
  consecutive_losses?: number;
  trades_count?: number;
  trading_day?: string | null;
}

export interface TradingIncident {
  id: number;
  incident_type?: string;
  severity?: string;
  title?: string;
  detail?: string | null;
  status?: string;
  created_at?: string;
  resolved_at?: string | null;
}

export interface ReconciliationRun {
  id: number;
  status: string;
  open_positions_broker?: number | null;
  open_positions_db?: number | null;
  repaired?: number;
  mismatches?: Record<string, unknown>;
  started_at?: string;
  finished_at?: string | null;
}

export interface ExecutionReceipt {
  id: number;
  idempotency_key: string;
  client_order_id?: string | null;
  broker: string;
  broker_order_id?: string | null;
  broker_position_id?: string | null;
  broker_deal_id?: string | null;
  submit_status: string;
  fill_status: string;
  requested_volume: number;
  filled_volume: number;
  avg_fill_price?: number | null;
  commission?: number;
  account_id?: string | null;
  server_time?: number | null;
  latency_ms?: number;
  raw_response_hash?: string | null;
  raw_response?: Record<string, unknown>;
  created_at?: string;
}

export const runtimeApi = {
  getStatus: (workspaceId: string, botId: string) =>
    api.get<RuntimeStatus>(`/v1/workspaces/${workspaceId}/bots/${botId}/runtime`),

  getDailyState: (workspaceId: string, botId: string) =>
    api.get<DailyState>(`/v1/workspaces/${workspaceId}/bots/${botId}/daily-state`),

  getIncidents: (workspaceId: string, botId: string, limit = 50) =>
    api.get<TradingIncident[]>(`/v1/workspaces/${workspaceId}/bots/${botId}/incidents`, {
      params: { limit },
    }),

  getReconciliationRuns: (workspaceId: string, botId: string, limit = 50) =>
    api.get<ReconciliationRun[]>(`/v1/workspaces/${workspaceId}/bots/${botId}/reconciliation-runs`, {
      params: { limit },
    }),

  getExecutionReceipts: (workspaceId: string, botId: string, limit = 50) =>
    api.get<ExecutionReceipt[]>(`/v1/workspaces/${workspaceId}/bots/${botId}/execution-receipts`, {
      params: { limit },
    }),

  start: (workspaceId: string, botId: string) =>
    api.post(`/v1/workspaces/${workspaceId}/bots/${botId}/start`),

  stop: (workspaceId: string, botId: string) =>
    api.post(`/v1/workspaces/${workspaceId}/bots/${botId}/stop`),

  pause: (workspaceId: string, botId: string) =>
    api.post(`/v1/workspaces/${workspaceId}/bots/${botId}/pause`),

  resume: (workspaceId: string, botId: string) =>
    api.post(`/v1/workspaces/${workspaceId}/bots/${botId}/resume`),

  killSwitch: (workspaceId: string, botId: string) =>
    api.post(`/v1/workspaces/${workspaceId}/bots/${botId}/kill-switch`),
};

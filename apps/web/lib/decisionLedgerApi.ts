import { api } from './api';

export interface GateEvent {
  id: number;
  bot_instance_id: string;
  signal_id: string;
  idempotency_key: string;
  gate_action: 'ALLOW' | 'SKIP' | 'BLOCK';
  gate_reason: string;
  gate_details?: Record<string, unknown>;
  created_at: string;
}

export interface DecisionLedgerEntry {
  id: number;
  bot_instance_id: string;
  signal_id: string;
  cycle_id?: string;
  brain_action: string;
  brain_reason?: string;
  brain_score?: number;
  stage_decisions?: unknown[];
  created_at: string;
}

export const decisionLedgerApi = {
  getDecisions: (workspaceId: string, botId: string, limit = 50) =>
    api.get<DecisionLedgerEntry[]>(
      `/v1/workspaces/${workspaceId}/bots/${botId}/decision-ledger`,
      { params: { limit } }
    ),

  getGateEvents: (workspaceId: string, botId: string, limit = 50) =>
    api.get<GateEvent[]>(
      `/v1/workspaces/${workspaceId}/bots/${botId}/gate-events`,
      { params: { limit } }
    ),
};

import { api } from './api';

export interface BrokerHealth {
  status: 'healthy' | 'degraded' | 'disconnected';
  provider_mode?: string;
  account_id?: string;
  latency_ms?: number;
  last_ok_at?: string;
  reason?: string;
}

export const brokerApi = {
  getHealth: (workspaceId: string, botId: string) =>
    api.get<BrokerHealth>(`/v1/workspaces/${workspaceId}/bots/${botId}/broker-health`),

  getConnections: (workspaceId: string) =>
    api.get(`/v1/workspaces/${workspaceId}/broker-connections`),
};

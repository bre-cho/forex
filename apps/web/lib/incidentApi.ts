import { api } from './api';

export interface TradingIncident {
  id: number;
  bot_instance_id: string;
  incident_type: string;
  severity: 'info' | 'warning' | 'critical';
  title: string;
  detail?: string;
  status: 'open' | 'acknowledged' | 'resolved';
  resolved_at?: string;
  created_at: string;
}

export const incidentApi = {
  list: (workspaceId: string, params?: { bot_instance_id?: string; status?: string; limit?: number }) =>
    api.get<TradingIncident[]>(`/v1/workspaces/${workspaceId}/incidents`, { params }),

  acknowledge: (workspaceId: string, incidentId: number) =>
    api.post(`/v1/workspaces/${workspaceId}/incidents/${incidentId}/acknowledge`),

  resolve: (workspaceId: string, incidentId: number) =>
    api.post(`/v1/workspaces/${workspaceId}/incidents/${incidentId}/resolve`),
};

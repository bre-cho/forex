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

export const runtimeApi = {
  getStatus: (workspaceId: string, botId: string) =>
    api.get<RuntimeStatus>(`/v1/workspaces/${workspaceId}/bots/${botId}/runtime`),

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

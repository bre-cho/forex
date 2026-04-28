import { api } from './api';

export interface DailyTradingState {
  trading_day: string;
  starting_equity?: number;
  current_equity?: number;
  daily_profit_amount: number;
  daily_loss_pct: number;
  trades_count: number;
  consecutive_losses: number;
  locked: boolean;
  lock_reason?: string;
  updated_at: string;
}

export const riskPolicyApi = {
  getDailyState: (workspaceId: string, botId: string) =>
    api.get<DailyTradingState>(`/v1/workspaces/${workspaceId}/bots/${botId}/daily-state`),

  getPolicy: (workspaceId: string, botId: string) =>
    api.get(`/v1/workspaces/${workspaceId}/bots/${botId}/policy`),

  updatePolicy: (workspaceId: string, botId: string, policy: Record<string, unknown>) =>
    api.put(`/v1/workspaces/${workspaceId}/bots/${botId}/policy`, policy),
};

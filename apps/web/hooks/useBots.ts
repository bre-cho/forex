'use client';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { botApi } from '@/lib/api';

export interface BotDailyState {
  bot_instance_id: string;
  locked: boolean;
  daily_profit_amount: number;
  daily_loss_pct: number;
  consecutive_losses: number;
  trades_count: number;
  trading_day: string | null;
  lock_reason?: string | null;
}

export interface BotIncident {
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

export function useBots(workspaceId: string) {
  return useQuery({
    queryKey: ['bots', workspaceId],
    queryFn: () => botApi.list(workspaceId).then((r) => r.data),
    enabled: !!workspaceId,
  });
}

export function useBot(workspaceId: string, botId: string) {
  return useQuery({
    queryKey: ['bot', workspaceId, botId],
    queryFn: () => botApi.get(workspaceId, botId).then((r) => r.data),
    enabled: !!workspaceId && !!botId,
  });
}

export function useBotRuntime(workspaceId: string, botId: string) {
  return useQuery({
    queryKey: ['bot-runtime', workspaceId, botId],
    queryFn: () => botApi.runtime(workspaceId, botId).then((r) => r.data),
    enabled: !!workspaceId && !!botId,
    refetchInterval: 5000,
  });
}

export function useBotDailyState(workspaceId: string, botId: string) {
  return useQuery({
    queryKey: ['bot-daily-state', workspaceId, botId],
    queryFn: () => botApi.dailyState(workspaceId, botId).then((r) => r.data as BotDailyState),
    enabled: !!workspaceId && !!botId,
    refetchInterval: 10000,
  });
}

export function useBotIncidents(workspaceId: string, botId: string) {
  return useQuery({
    queryKey: ['bot-incidents', workspaceId, botId],
    queryFn: () => botApi.incidents(workspaceId, botId).then((r) => r.data as BotIncident[]),
    enabled: !!workspaceId && !!botId,
    refetchInterval: 10000,
  });
}

export function useBotActions(workspaceId: string) {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['bots', workspaceId] });
    qc.invalidateQueries({ queryKey: ['bot-runtime', workspaceId] });
  };

  const startBot = useMutation({
    mutationFn: (botId: string) => botApi.start(workspaceId, botId),
    onSuccess: invalidate,
  });

  const stopBot = useMutation({
    mutationFn: (botId: string) => botApi.stop(workspaceId, botId),
    onSuccess: invalidate,
  });

  return { startBot, stopBot };
}

export function useBotLiveActions(workspaceId: string, botId: string) {
  const qc = useQueryClient();
  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['bot-runtime', workspaceId, botId] });
    qc.invalidateQueries({ queryKey: ['bot-daily-state', workspaceId, botId] });
    qc.invalidateQueries({ queryKey: ['bot-incidents', workspaceId, botId] });
  };

  const reconcileNow = useMutation({
    mutationFn: () => botApi.reconcileNow(workspaceId, botId).then((r) => r.data),
    onSuccess: invalidate,
  });

  const resetDailyLock = useMutation({
    mutationFn: () => botApi.resetDailyLock(workspaceId, botId).then((r) => r.data),
    onSuccess: invalidate,
  });

  const resolveIncident = useMutation({
    mutationFn: (incidentId: number) => botApi.resolveIncident(workspaceId, botId, incidentId).then((r) => r.data),
    onSuccess: invalidate,
  });

  return { reconcileNow, resetDailyLock, resolveIncident };
}

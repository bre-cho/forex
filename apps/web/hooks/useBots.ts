'use client';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { botApi } from '@/lib/api';

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

export function useBotActions(workspaceId: string) {
  const qc = useQueryClient();
  const invalidate = () => qc.invalidateQueries({ queryKey: ['bots', workspaceId] });

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

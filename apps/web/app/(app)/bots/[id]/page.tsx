'use client';
import { useEffect, useState } from 'react';
import { useBot, useBotRuntime, useBotActions } from '@/hooks/useBots';
import { useBotWebSocket } from '@/hooks/useWebSocket';
import { workspaceApi } from '@/lib/api';

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    running: 'bg-green-900 text-green-300',
    stopped: 'bg-gray-700 text-gray-300',
    paused: 'bg-yellow-900 text-yellow-300',
    error: 'bg-red-900 text-red-300',
  };
  return (
    <span
      className={`px-2 py-0.5 rounded text-xs font-medium ${
        colors[status] ?? 'bg-gray-700 text-gray-300'
      }`}
    >
      {status}
    </span>
  );
}

export default function BotDetailPage({ params }: { params: { id: string } }) {
  const botId = params.id;
  const [workspaceId, setWorkspaceId] = useState<string>('');
  const [wsLoading, setWsLoading] = useState(true);

  // Resolve workspace from the first available workspace
  useEffect(() => {
    workspaceApi.list().then((r) => {
      const ws = r.data as { id: string }[];
      if (ws?.length) setWorkspaceId(ws[0].id);
    }).finally(() => setWsLoading(false));
  }, []);

  const { data: bot, isLoading: botLoading, error: botError } = useBot(workspaceId, botId);
  const { data: runtime, isLoading: runtimeLoading } = useBotRuntime(workspaceId, botId);
  const { startBot, stopBot } = useBotActions(workspaceId);
  const { data: wsData, isConnected } = useBotWebSocket(botId);

  const loading = wsLoading || botLoading;

  if (loading) {
    return (
      <div>
        <h1 className="text-3xl font-bold mb-6">Bot</h1>
        <div className="animate-pulse space-y-4">
          <div className="h-8 bg-surface-muted rounded w-48" />
          <div className="h-32 bg-surface-muted rounded" />
        </div>
      </div>
    );
  }

  if (botError || !bot) {
    return (
      <div>
        <h1 className="text-3xl font-bold mb-6">Bot: {botId}</h1>
        <div className="bg-red-900/40 border border-red-700 rounded-xl p-4 text-red-300">
          Bot not found or you do not have access.
        </div>
      </div>
    );
  }

  const isRunning = bot.status === 'running';

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold">{bot.name}</h1>
          <div className="flex items-center gap-3 mt-1">
            <StatusBadge status={bot.status} />
            <span className="text-sm text-gray-400">
              {bot.symbol} · {bot.timeframe} · {bot.mode}
            </span>
          </div>
        </div>
        <div className="flex gap-2">
          {!isRunning ? (
            <button
              onClick={() => startBot.mutate(botId)}
              disabled={startBot.isPending}
              className="px-4 py-2 bg-green-700 hover:bg-green-600 text-white rounded-lg text-sm font-medium disabled:opacity-50"
            >
              {startBot.isPending ? 'Starting…' : '▶ Start'}
            </button>
          ) : (
            <button
              onClick={() => stopBot.mutate(botId)}
              disabled={stopBot.isPending}
              className="px-4 py-2 bg-red-700 hover:bg-red-600 text-white rounded-lg text-sm font-medium disabled:opacity-50"
            >
              {stopBot.isPending ? 'Stopping…' : '■ Stop'}
            </button>
          )}
        </div>
      </div>

      {/* WS connection indicator */}
      <div className="flex items-center gap-2 mb-6">
        <div className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-400' : 'bg-gray-500'}`} />
        <span className="text-xs text-gray-400">
          {isConnected ? 'WebSocket connected — live updates active' : 'WebSocket disconnected'}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {/* Runtime status */}
        <div className="bg-surface-muted rounded-xl p-4">
          <h3 className="text-lg font-semibold mb-3">Runtime Status</h3>
          {runtimeLoading ? (
            <div className="animate-pulse h-16 bg-gray-800 rounded" />
          ) : runtime ? (
            <dl className="grid grid-cols-2 gap-2 text-sm">
              {Object.entries(runtime as Record<string, unknown>).map(([k, v]) => (
                <div key={k}>
                  <dt className="text-gray-500 text-xs uppercase">{k}</dt>
                  <dd className="text-white">{String(v)}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-gray-500 text-sm">Bot not currently running.</p>
          )}
        </div>

        {/* Live WS data */}
        <div className="bg-surface-muted rounded-xl p-4">
          <h3 className="text-lg font-semibold mb-3">Live Feed</h3>
          {wsData ? (
            <pre className="text-xs text-gray-300 overflow-auto max-h-40">
              {JSON.stringify(wsData, null, 2)}
            </pre>
          ) : (
            <p className="text-gray-500 text-sm">
              {isConnected ? 'Waiting for data…' : 'Start the bot to receive live updates.'}
            </p>
          )}
        </div>

        {/* Bot config */}
        <div className="bg-surface-muted rounded-xl p-4 md:col-span-2">
          <h3 className="text-lg font-semibold mb-3">Details</h3>
          <dl className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            {[
              ['ID', bot.id],
              ['Symbol', bot.symbol],
              ['Timeframe', bot.timeframe],
              ['Mode', bot.mode],
              ['Status', bot.status],
              ['Strategy', bot.strategy_id ?? '—'],
              ['Broker', bot.broker_connection_id ?? '—'],
              ['Created', new Date(bot.created_at).toLocaleDateString()],
            ].map(([label, value]) => (
              <div key={label}>
                <dt className="text-xs text-gray-500 uppercase tracking-wide">{label}</dt>
                <dd className="text-white mt-0.5 truncate">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
    </div>
  );
}


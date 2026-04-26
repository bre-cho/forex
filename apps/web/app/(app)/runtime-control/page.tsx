'use client';

import { useEffect, useState } from 'react';
import { botApi, workspaceApi } from '@/lib/api';

type Workspace = { id: string; name: string };
type Bot = {
  id: string;
  name: string;
  symbol: string;
  timeframe: string;
  mode: string;
  status: string;
};

type Runtime = {
  status?: string;
  started_at?: number;
  error_message?: string | null;
  metadata?: Record<string, unknown>;
};

function toVietnameseStatus(status?: string): string {
  const map: Record<string, string> = {
    running: 'đang_chạy',
    stopped: 'đã_dừng',
    paused: 'tạm_dừng',
    error: 'lỗi',
    starting: 'đang_khởi_động',
    not_running: 'chưa_chạy',
    unavailable: 'không_khả_dụng',
    healthy: 'ổn_định',
    degraded: 'suy_giảm',
    disconnected: 'mất_kết_nối',
    connected: 'đã_kết_nối',
  };
  if (!status) return 'không_rõ';
  return map[String(status).toLowerCase()] ?? String(status);
}

export default function RuntimeControlPage() {
  const [workspaceId, setWorkspaceId] = useState('');
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [bots, setBots] = useState<Bot[]>([]);
  const [runtimeByBot, setRuntimeByBot] = useState<Record<string, Runtime>>({});
  const [loading, setLoading] = useState(true);

  async function refresh() {
    if (!workspaceId) return;
    setLoading(true);
    const botRes = await botApi.list(workspaceId);
    const list = botRes.data as Bot[];
    setBots(list);

    const runtimeEntries = await Promise.all(
      list.map(async (bot) => {
        try {
          const runtime = await botApi.runtime(workspaceId, bot.id);
          return [bot.id, runtime.data as Runtime] as const;
        } catch {
          return [bot.id, { status: 'không_khả_dụng' } as Runtime] as const;
        }
      })
    );
    setRuntimeByBot(Object.fromEntries(runtimeEntries));
    setLoading(false);
  }

  useEffect(() => {
    async function bootstrap() {
      const wsRes = await workspaceApi.list();
      const ws = wsRes.data as Workspace[];
      setWorkspaces(ws);
      if (ws.length) setWorkspaceId(ws[0].id);
      setLoading(false);
    }
    bootstrap().catch(() => setLoading(false));
  }, []);

  useEffect(() => {
    refresh().catch(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId]);

  async function act(action: 'start' | 'stop' | 'pause' | 'resume', botId: string) {
    if (!workspaceId) return;
    if (action === 'start') await botApi.start(workspaceId, botId);
    if (action === 'stop') await botApi.stop(workspaceId, botId);
    if (action === 'pause') await botApi.pause(workspaceId, botId);
    if (action === 'resume') await botApi.resume(workspaceId, botId);
    await refresh();
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-3xl font-bold">Điều khiển sức khỏe runtime</h1>
        <button onClick={() => refresh()} className="px-3 py-2 bg-brand rounded-lg text-sm">Làm mới</button>
      </div>

      <div className="mb-4">
        <select
          className="bg-surface-muted rounded-lg px-3 py-2"
          value={workspaceId}
          onChange={(e) => setWorkspaceId(e.target.value)}
        >
          {workspaces.map((ws) => (
            <option key={ws.id} value={ws.id}>{ws.name}</option>
          ))}
        </select>
      </div>

      {loading ? (
        <p className="text-gray-400">Đang tải runtime...</p>
      ) : bots.length === 0 ? (
        <p className="text-gray-400">Không tìm thấy bot.</p>
      ) : (
        <div className="space-y-3">
          {bots.map((bot) => {
            const runtime = runtimeByBot[bot.id] || { status: 'not_running' };
            return (
              <div key={bot.id} className="bg-surface-muted rounded-xl p-4">
                <div className="flex items-start justify-between gap-4">
                  <div>
                    <div className="font-semibold">{bot.name}</div>
                    <div className="text-xs text-gray-400">{bot.symbol} · {bot.timeframe} · {bot.mode}</div>
                    <div className="text-sm mt-2">Trạng thái bot: {toVietnameseStatus(bot.status)}</div>
                    <div className="text-sm">Trạng thái bộ máy: {toVietnameseStatus(runtime.status)}</div>
                    {runtime.error_message && (
                      <div className="text-red-300 text-xs mt-1">Lỗi: {runtime.error_message}</div>
                    )}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button onClick={() => act('start', bot.id)} className="px-3 py-2 rounded bg-green-700 text-white text-xs">Bắt đầu</button>
                    <button onClick={() => act('pause', bot.id)} className="px-3 py-2 rounded bg-yellow-700 text-white text-xs">Tạm dừng</button>
                    <button onClick={() => act('resume', bot.id)} className="px-3 py-2 rounded bg-blue-700 text-white text-xs">Tiếp tục</button>
                    <button onClick={() => act('stop', bot.id)} className="px-3 py-2 rounded bg-red-700 text-white text-xs">Dừng</button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

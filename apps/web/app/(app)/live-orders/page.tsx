'use client';

import { useEffect, useMemo, useState } from 'react';
import { botApi, workspaceApi } from '@/lib/api';

type Workspace = { id: string; name: string };
type Bot = { id: string; name: string; symbol: string; status: string };
type OrderRow = {
  id: string;
  broker_order_id: string;
  symbol: string;
  side: string;
  order_type: string;
  volume: number;
  price: number | null;
  status: string;
  created_at: string;
};

type RuntimeSnapshot = {
  status?: string;
  metadata?: {
    broker_health?: { status?: string; reason?: string };
    broker_connected?: boolean;
  };
};

function toVietnameseStatus(status?: string): string {
  const map: Record<string, string> = {
    running: 'đang_chạy',
    stopped: 'đã_dừng',
    paused: 'tạm_dừng',
    error: 'lỗi',
    starting: 'đang_khởi_động',
    healthy: 'ổn_định',
    degraded: 'suy_giảm',
    disconnected: 'mất_kết_nối',
    connected: 'đã_kết_nối',
    not_running: 'chưa_chạy',
  };
  if (!status) return 'không_rõ';
  return map[String(status).toLowerCase()] ?? String(status);
}

export default function LiveOrdersPage() {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [bots, setBots] = useState<Bot[]>([]);
  const [workspaceId, setWorkspaceId] = useState('');
  const [botId, setBotId] = useState('');
  const [orders, setOrders] = useState<OrderRow[]>([]);
  const [runtime, setRuntime] = useState<RuntimeSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const selectedBot = useMemo(() => bots.find((b) => b.id === botId), [bots, botId]);

  useEffect(() => {
    async function bootstrap() {
      try {
        const wsRes = await workspaceApi.list();
        const ws = wsRes.data as Workspace[];
        setWorkspaces(ws);
        if (!ws.length) {
          setLoading(false);
          return;
        }
        const initialWs = ws[0].id;
        setWorkspaceId(initialWs);
      } catch {
        setError('Không tải được danh sách workspace');
        setLoading(false);
      }
    }
    bootstrap();
  }, []);

  useEffect(() => {
    async function loadBots() {
      if (!workspaceId) return;
      try {
        const botRes = await botApi.list(workspaceId);
        const items = botRes.data as Bot[];
        setBots(items);
        if (items.length && !botId) {
          setBotId(items[0].id);
        }
      } catch {
        setError('Không tải được danh sách bot');
      }
    }
    loadBots();
  }, [workspaceId]);

  async function refresh() {
    if (!workspaceId || !botId) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [orderRes, runtimeRes] = await Promise.all([
        botApi.orders(workspaceId, botId),
        botApi.runtime(workspaceId, botId),
      ]);
      setOrders(orderRes.data as OrderRow[]);
      setRuntime(runtimeRes.data as RuntimeSnapshot);
    } catch {
      setError('Không tải được lệnh trực tiếp hoặc runtime');
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, botId]);

  if (!workspaces.length && !loading) {
    return <div className="text-gray-400">Không tìm thấy workspace.</div>;
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-3xl font-bold">Lệnh trực tiếp</h1>
        <button
          onClick={refresh}
          className="px-3 py-2 rounded-lg bg-brand text-white text-sm"
        >
          Làm mới
        </button>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
        <select
          className="bg-surface-muted rounded-lg px-3 py-2"
          value={workspaceId}
          onChange={(e) => setWorkspaceId(e.target.value)}
        >
          {workspaces.map((ws) => (
            <option key={ws.id} value={ws.id}>{ws.name}</option>
          ))}
        </select>

        <select
          className="bg-surface-muted rounded-lg px-3 py-2"
          value={botId}
          onChange={(e) => setBotId(e.target.value)}
        >
          {bots.map((bot) => (
            <option key={bot.id} value={bot.id}>{bot.name}</option>
          ))}
        </select>

        <div className="bg-surface-muted rounded-lg px-3 py-2 text-sm">
          Trạng thái bộ máy: <span className="font-semibold">{toVietnameseStatus(runtime?.status)}</span>
          <div className="text-xs text-gray-400 mt-1">
            Sức khỏe kết nối sàn: {toVietnameseStatus(runtime?.metadata?.broker_health?.status)}
          </div>
        </div>
      </div>

      {error && <div className="text-red-300 bg-red-900/40 p-3 rounded mb-4">{error}</div>}

      <div className="bg-surface-muted rounded-xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-black/20 text-gray-300">
            <tr>
              <th className="text-left px-3 py-2">Thời gian</th>
              <th className="text-left px-3 py-2">Lệnh</th>
              <th className="text-left px-3 py-2">Cặp</th>
              <th className="text-left px-3 py-2">Chiều</th>
              <th className="text-left px-3 py-2">Loại</th>
              <th className="text-right px-3 py-2">Khối lượng</th>
              <th className="text-right px-3 py-2">Giá</th>
              <th className="text-left px-3 py-2">Trạng thái</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={8} className="px-3 py-4 text-gray-400">Đang tải...</td></tr>
            ) : orders.length === 0 ? (
              <tr><td colSpan={8} className="px-3 py-4 text-gray-400">Chưa có lệnh.</td></tr>
            ) : (
              orders.map((o) => (
                <tr key={o.id} className="border-t border-white/5">
                  <td className="px-3 py-2">{new Date(o.created_at).toLocaleString()}</td>
                  <td className="px-3 py-2">{o.broker_order_id || o.id}</td>
                  <td className="px-3 py-2">{o.symbol}</td>
                  <td className="px-3 py-2">{o.side}</td>
                  <td className="px-3 py-2">{o.order_type}</td>
                  <td className="px-3 py-2 text-right">{o.volume.toFixed(2)}</td>
                  <td className="px-3 py-2 text-right">{o.price?.toFixed(5) ?? '-'}</td>
                  <td className="px-3 py-2">{o.status}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>

      {selectedBot && (
        <p className="text-xs text-gray-400 mt-3">
          Bot đang chọn: {selectedBot.name} ({selectedBot.symbol})
        </p>
      )}
    </div>
  );
}

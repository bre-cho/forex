'use client';

import { useEffect, useMemo, useState } from 'react';
import { botApi, workspaceApi } from '@/lib/api';

type Workspace = { id: string; name: string };
type Bot = { id: string; name: string };
type TradeRow = {
  id: string;
  broker_trade_id: string;
  symbol: string;
  side: string;
  volume: number;
  entry_price: number;
  exit_price: number | null;
  stop_loss: number | null;
  take_profit: number | null;
  pnl: number | null;
  opened_at: string;
  closed_at: string | null;
};

export default function TradesPage() {
  const [workspaceId, setWorkspaceId] = useState('');
  const [botId, setBotId] = useState('');
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [bots, setBots] = useState<Bot[]>([]);
  const [trades, setTrades] = useState<TradeRow[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    async function loadWorkspaces() {
      const wsRes = await workspaceApi.list();
      const ws = wsRes.data as Workspace[];
      setWorkspaces(ws);
      if (ws.length) setWorkspaceId(ws[0].id);
      setLoading(false);
    }
    loadWorkspaces().catch(() => setLoading(false));
  }, []);

  useEffect(() => {
    async function loadBots() {
      if (!workspaceId) return;
      const botRes = await botApi.list(workspaceId);
      const list = botRes.data as Bot[];
      setBots(list);
      if (list.length && !botId) setBotId(list[0].id);
    }
    loadBots().catch(() => undefined);
  }, [workspaceId]);

  useEffect(() => {
    async function loadTrades() {
      if (!workspaceId || !botId) return;
      setLoading(true);
      try {
        const res = await botApi.trades(workspaceId, botId);
        setTrades(res.data as TradeRow[]);
      } finally {
        setLoading(false);
      }
    }
    loadTrades().catch(() => setLoading(false));
  }, [workspaceId, botId]);

  const openTrades = useMemo(() => trades.filter((t) => !t.closed_at), [trades]);
  const closedTrades = useMemo(() => trades.filter((t) => !!t.closed_at), [trades]);

  return (
    <div>
      <h1 className="text-3xl font-bold mb-6">Giao dịch mở / đã đóng</h1>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mb-5">
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
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
        <section className="bg-surface-muted rounded-xl p-4">
          <h2 className="font-semibold mb-3">Lệnh mở ({openTrades.length})</h2>
          {loading ? (
            <p className="text-gray-400">Đang tải...</p>
          ) : openTrades.length === 0 ? (
            <p className="text-gray-400">Không có lệnh mở.</p>
          ) : (
            <div className="space-y-2">
              {openTrades.map((t) => (
                <div key={t.id} className="border border-white/10 rounded-lg p-3 text-sm">
                  <div className="font-medium">{t.side} {t.symbol} · {t.volume.toFixed(2)}</div>
                  <div className="text-gray-400">Giá vào: {t.entry_price.toFixed(5)}</div>
                  <div className="text-gray-400">SL/TP: {t.stop_loss?.toFixed(5) ?? '-'} / {t.take_profit?.toFixed(5) ?? '-'}</div>
                </div>
              ))}
            </div>
          )}
        </section>

        <section className="bg-surface-muted rounded-xl p-4">
          <h2 className="font-semibold mb-3">Lệnh đã đóng ({closedTrades.length})</h2>
          {loading ? (
            <p className="text-gray-400">Đang tải...</p>
          ) : closedTrades.length === 0 ? (
            <p className="text-gray-400">Không có lệnh đã đóng.</p>
          ) : (
            <div className="space-y-2 max-h-[520px] overflow-auto pr-1">
              {closedTrades.map((t) => (
                <div key={t.id} className="border border-white/10 rounded-lg p-3 text-sm">
                  <div className="font-medium">{t.side} {t.symbol} · {t.volume.toFixed(2)}</div>
                  <div className="text-gray-400">Giá vào/ra: {t.entry_price.toFixed(5)} / {t.exit_price?.toFixed(5) ?? '-'}</div>
                  <div className={t.pnl && t.pnl >= 0 ? 'text-green-300' : 'text-red-300'}>
                    Lãi/Lỗ: {t.pnl ?? 0}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

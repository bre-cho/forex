'use client';
import { useEffect, useState } from 'react';
import { useAuthStore } from '@/lib/auth';
import { workspaceApi, analyticsApi, botApi } from '@/lib/api';

interface Stats {
  active_bots: number;
  total_pnl: number;
  win_rate: number;
  total_trades: number;
}

export default function DashboardPage() {
  const { user } = useAuthStore();
  const [stats, setStats] = useState<Stats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function load() {
      try {
        setLoading(true);
        // Pick the first workspace available
        const wsRes = await workspaceApi.list();
        const workspaces = wsRes.data as { id: string; name: string }[];
        if (!workspaces || workspaces.length === 0) {
          setStats({ active_bots: 0, total_pnl: 0, win_rate: 0, open_trades: 0 });
          return;
        }
        const wsId = workspaces[0].id;
        const [botsRes, summaryRes] = await Promise.all([
          botApi.list(wsId),
          analyticsApi.summary(wsId),
        ]);
        const bots = botsRes.data as { status: string }[];
        const summary = summaryRes.data as {
          total_pnl: number;
          win_rate: number;
        };
        setStats({
          active_bots: bots.filter((b) => b.status === 'running').length,
          total_pnl: summary.total_pnl ?? 0,
          win_rate: summary.win_rate ?? 0,
          total_trades: summary.total_trades ?? 0,
        });
      } catch (err: unknown) {
        setError('Không tải được dữ liệu tổng quan.');
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const statCards = stats
    ? [
        { label: 'Bot đang chạy', value: stats.active_bots.toString() },
        {
          label: 'Tổng PnL',
          value: `$${stats.total_pnl >= 0 ? '+' : ''}${stats.total_pnl.toFixed(2)}`,
        },
        { label: 'Tỷ lệ thắng', value: `${(stats.win_rate * 100).toFixed(1)}%` },
        { label: 'Tổng giao dịch', value: stats.total_trades.toString() },
      ]
    : [
        { label: 'Bot đang chạy', value: '—' },
        { label: 'Tổng PnL', value: '—' },
        { label: 'Tỷ lệ thắng', value: '—' },
        { label: 'Tổng giao dịch', value: '—' },
      ];

  return (
    <div>
      <h1 className="text-3xl font-bold mb-2">Tổng quan</h1>
      {user && (
        <p className="text-gray-400 mb-6 text-sm">Chào mừng quay lại, {user.full_name || user.email}</p>
      )}

      {error && (
        <div className="mb-4 p-3 bg-red-900/40 border border-red-700 rounded-lg text-red-300 text-sm">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
        {statCards.map((stat) => (
          <div key={stat.label} className="bg-surface-muted rounded-xl p-4">
            <p className="text-gray-400 text-sm">{stat.label}</p>
            <p className={`text-2xl font-bold mt-1 ${loading ? 'animate-pulse text-gray-600' : ''}`}>
              {loading ? '…' : stat.value}
            </p>
          </div>
        ))}
      </div>

      {!loading && stats?.active_bots === 0 && (
        <div className="bg-surface-muted rounded-xl p-6 text-center">
          <p className="text-gray-400 mb-2">Chưa có bot nào đang chạy.</p>
          <a href="/bots" className="text-brand underline text-sm">
            Đi tới Bot →
          </a>
        </div>
      )}
    </div>
  );
}


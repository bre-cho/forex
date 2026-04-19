'use client';
import { useEffect, useState } from 'react';
import { useAuthStore } from '@/lib/auth';
import { workspaceApi, analyticsApi, botApi } from '@/lib/api';

interface Stats {
  active_bots: number;
  total_pnl: number;
  win_rate: number;
  open_trades: number;
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
          open_trades: bots.filter((b) => b.status === 'running').length,
        });
      } catch (err: unknown) {
        setError('Failed to load dashboard data.');
      } finally {
        setLoading(false);
      }
    }
    load();
  }, []);

  const statCards = stats
    ? [
        { label: 'Active Bots', value: stats.active_bots.toString() },
        {
          label: 'Total PnL',
          value: `$${stats.total_pnl >= 0 ? '+' : ''}${stats.total_pnl.toFixed(2)}`,
        },
        { label: 'Win Rate', value: `${(stats.win_rate * 100).toFixed(1)}%` },
        { label: 'Open Trades', value: stats.open_trades.toString() },
      ]
    : [
        { label: 'Active Bots', value: '—' },
        { label: 'Total PnL', value: '—' },
        { label: 'Win Rate', value: '—' },
        { label: 'Open Trades', value: '—' },
      ];

  return (
    <div>
      <h1 className="text-3xl font-bold mb-2">Dashboard</h1>
      {user && (
        <p className="text-gray-400 mb-6 text-sm">Welcome back, {user.full_name || user.email}</p>
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
          <p className="text-gray-400 mb-2">No bots running yet.</p>
          <a href="/bots" className="text-brand underline text-sm">
            Go to Bots →
          </a>
        </div>
      )}
    </div>
  );
}


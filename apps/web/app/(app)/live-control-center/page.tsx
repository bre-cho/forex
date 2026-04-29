'use client';

import Link from 'next/link';
import { useEffect, useMemo, useState } from 'react';

import { botApi, workspaceApi } from '@/lib/api';

type BotRow = {
  id: string;
  name: string;
  mode: string;
  status: string;
  symbol: string;
  timeframe: string;
};

function statusClass(status: string): string {
  const value = String(status || '').toLowerCase();
  if (value === 'running') return 'bg-emerald-950 text-emerald-300 border border-emerald-800';
  if (value === 'error') return 'bg-red-950 text-red-300 border border-red-800';
  if (value === 'paused') return 'bg-amber-950 text-amber-300 border border-amber-800';
  return 'bg-slate-800 text-slate-300 border border-slate-700';
}

export default function LiveControlCenterPage() {
  const [workspaceId, setWorkspaceId] = useState<string>('');
  const [bots, setBots] = useState<BotRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      setLoading(true);
      setError('');
      try {
        const wsResp = await workspaceApi.list();
        const workspaces = (wsResp.data || []) as Array<{ id: string }>;
        const ws = workspaces[0];
        if (!ws?.id) {
          if (mounted) setBots([]);
          return;
        }
        if (mounted) setWorkspaceId(ws.id);
        const botResp = await botApi.list(ws.id);
        if (!mounted) return;
        const rows = (botResp.data || []) as BotRow[];
        setBots(rows);
      } catch (err: any) {
        if (!mounted) return;
        setError(String(err?.response?.data?.detail || err?.message || 'Không tải được Live Control Center'));
      } finally {
        if (mounted) setLoading(false);
      }
    };
    load();
    return () => {
      mounted = false;
    };
  }, []);

  const liveBots = useMemo(() => bots.filter((b) => String(b.mode).toLowerCase() === 'live'), [bots]);
  const runningCount = useMemo(() => liveBots.filter((b) => String(b.status).toLowerCase() === 'running').length, [liveBots]);
  const errorCount = useMemo(() => liveBots.filter((b) => String(b.status).toLowerCase() === 'error').length, [liveBots]);

  return (
    <div>
      <div className='flex items-start justify-between gap-4 mb-6'>
        <div>
          <h1 className='text-3xl font-bold'>Trung tâm điều khiển Live Trading</h1>
          <p className='text-sm text-gray-400 mt-1'>Một màn hình để vào runtime, daily state, incidents và thao tác reconcile/lock cho bot live.</p>
        </div>
        <div className='text-xs text-gray-500'>workspace: {workspaceId || '—'}</div>
      </div>

      <div className='grid grid-cols-1 md:grid-cols-3 gap-3 mb-6'>
        <div className='rounded-xl border border-slate-800 bg-slate-950/40 p-4'>
          <div className='text-xs text-gray-500 uppercase tracking-wide'>Live bots</div>
          <div className='text-2xl font-semibold mt-1'>{liveBots.length}</div>
        </div>
        <div className='rounded-xl border border-slate-800 bg-slate-950/40 p-4'>
          <div className='text-xs text-gray-500 uppercase tracking-wide'>Running</div>
          <div className='text-2xl font-semibold mt-1 text-emerald-300'>{runningCount}</div>
        </div>
        <div className='rounded-xl border border-slate-800 bg-slate-950/40 p-4'>
          <div className='text-xs text-gray-500 uppercase tracking-wide'>Error</div>
          <div className='text-2xl font-semibold mt-1 text-red-300'>{errorCount}</div>
        </div>
      </div>

      {loading ? <p className='text-gray-400'>Đang tải dữ liệu...</p> : null}
      {error ? <div className='rounded-lg border border-red-800 bg-red-950/60 px-3 py-2 text-sm text-red-300 mb-4'>{error}</div> : null}

      {!loading && !error && liveBots.length === 0 ? (
        <div className='rounded-xl border border-slate-800 bg-slate-950/40 p-4 text-gray-400'>
          Chưa có bot live trong workspace này.
        </div>
      ) : null}

      <div className='space-y-3'>
        {liveBots.map((bot) => (
          <div key={bot.id} className='rounded-xl border border-slate-800 bg-slate-950/40 p-4'>
            <div className='flex flex-col gap-3 md:flex-row md:items-center md:justify-between'>
              <div>
                <div className='flex items-center gap-2'>
                  <h2 className='text-lg font-semibold'>{bot.name}</h2>
                  <span className={`rounded-md px-2 py-0.5 text-xs font-medium ${statusClass(bot.status)}`}>{bot.status}</span>
                </div>
                <div className='text-sm text-gray-400 mt-1'>
                  {bot.symbol} · {bot.timeframe} · {bot.mode}
                </div>
              </div>
              <div className='flex flex-wrap gap-2'>
                <Link href={`/bots/${bot.id}`} className='px-3 py-2 rounded-lg bg-blue-800 hover:bg-blue-700 text-white text-sm font-medium'>
                  Mở bot control
                </Link>
                <Link href='/runtime-control' className='px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-white text-sm font-medium'>
                  Runtime panel
                </Link>
                <Link href='/live-orders' className='px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-white text-sm font-medium'>
                  Live orders
                </Link>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

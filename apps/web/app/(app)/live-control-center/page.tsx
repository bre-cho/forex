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

type OpsDashboard = {
  runtime?: { status?: string; error_message?: string; metadata?: Record<string, unknown> };
  daily_state?: { locked?: boolean; daily_profit_amount?: number; daily_loss_pct?: number; lock_reason?: string };
  open_incidents?: Array<{ id: number; severity?: string; title?: string }>;
  latest_account_snapshot?: { equity?: number; free_margin?: number; margin_level?: number; currency?: string };
  latest_experiment?: { stage?: string; version?: number; updated_at?: string };
};

type ProviderCertificationRecord = {
  provider?: string;
  mode?: string;
  live_certified?: boolean;
  certification_hash?: string | null;
  certified_at?: string | null;
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
  const [meta, setMeta] = useState<Record<string, { incidents: number; reconStatus: string; dailyLocked: boolean }>>({});
  const [ops, setOps] = useState<Record<string, OpsDashboard>>({});
  const [certByBot, setCertByBot] = useState<Record<string, ProviderCertificationRecord | null>>({});
  const [actionMsg, setActionMsg] = useState<string>('');
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
        const liveRows = rows.filter((b) => String(b.mode).toLowerCase() === 'live');
        const entries = await Promise.all(
          liveRows.map(async (bot) => {
            try {
              const [incResp, recResp, dailyResp, opsResp, certResp] = await Promise.all([
                botApi.incidents(ws.id, bot.id, 20),
                botApi.reconciliationRuns(ws.id, bot.id, 1),
                botApi.dailyState(ws.id, bot.id),
                botApi.operationsDashboard(ws.id, bot.id),
                botApi.providerCertificationRecords(ws.id, bot.id, 1),
              ]);
              const incidents = ((incResp.data || []) as Array<{ status?: string }>).filter((x) => String(x.status || '').toLowerCase() !== 'resolved').length;
              const recStatus = ((recResp.data || []) as Array<{ status?: string }>)[0]?.status || 'n/a';
              const dailyLocked = Boolean((dailyResp.data || {}).locked);
              const certItems = ((certResp.data || {}) as { items?: ProviderCertificationRecord[] }).items || [];
              const latestCert = certItems.length > 0 ? certItems[0] : null;
              return [bot.id, { incidents, reconStatus: String(recStatus), dailyLocked, ops: (opsResp.data || {}) as OpsDashboard, cert: latestCert }] as const;
            } catch {
              return [bot.id, { incidents: 0, reconStatus: 'n/a', dailyLocked: false, ops: {} as OpsDashboard, cert: null }] as const;
            }
          })
        );
        if (mounted) {
          setMeta(Object.fromEntries(entries.map(([id, item]) => [id, { incidents: item.incidents, reconStatus: item.reconStatus, dailyLocked: item.dailyLocked }])));
          setOps(Object.fromEntries(entries.map(([id, item]) => [id, item.ops])));
          setCertByBot(Object.fromEntries(entries.map(([id, item]) => [id, item.cert])));
        }
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

  const runAction = async (botId: string, action: 'reconcile' | 'kill' | 'unkill') => {
    if (!workspaceId) return;
    setActionMsg('');
    try {
      if (action === 'reconcile') await botApi.reconcileNow(workspaceId, botId);
      if (action === 'kill') await botApi.killSwitch(workspaceId, botId);
      if (action === 'unkill') await botApi.resetKillSwitch(workspaceId, botId);
      setActionMsg(`Action ${action} thành công cho bot ${botId}`);
    } catch (err: any) {
      setActionMsg(String(err?.response?.data?.detail || err?.message || `Action ${action} thất bại`));
    }
  };

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
      {actionMsg ? <div className='rounded-lg border border-slate-700 bg-slate-900/70 px-3 py-2 text-sm text-slate-200 mb-4'>{actionMsg}</div> : null}

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
                <div className='text-xs text-gray-500 mt-2'>
                  recon: {meta[bot.id]?.reconStatus || 'n/a'} · incidents mở: {meta[bot.id]?.incidents ?? 0} · daily lock: {meta[bot.id]?.dailyLocked ? 'on' : 'off'}
                </div>
                <div className='text-xs text-gray-500 mt-1'>
                  runtime: {String(ops[bot.id]?.runtime?.status || 'n/a')} · stage: {String(ops[bot.id]?.latest_experiment?.stage || 'DRAFT')} v{String(ops[bot.id]?.latest_experiment?.version || '-')}
                </div>
                <div className='text-xs text-gray-500 mt-1'>
                  provider certification:{' '}
                  <span className={certByBot[bot.id]?.live_certified ? 'text-emerald-300' : 'text-red-300'}>
                    {certByBot[bot.id]?.live_certified ? 'certified' : 'uncertified'}
                  </span>
                  {' '}· provider: {String(certByBot[bot.id]?.provider || 'n/a')}
                  {' '}· mode: {String(certByBot[bot.id]?.mode || 'live')}
                  {' '}· at: {String(certByBot[bot.id]?.certified_at || 'n/a')}
                </div>
                <div className='text-xs text-gray-500 mt-1'>
                  cert hash: {String(certByBot[bot.id]?.certification_hash || 'n/a')}
                </div>
                <div className='text-xs text-gray-500 mt-1'>
                  equity: {ops[bot.id]?.latest_account_snapshot?.equity ?? 'n/a'} {ops[bot.id]?.latest_account_snapshot?.currency || ''} · free margin: {ops[bot.id]?.latest_account_snapshot?.free_margin ?? 'n/a'}
                </div>
                <div className='text-xs text-gray-500 mt-1'>
                  daily pnl: {ops[bot.id]?.daily_state?.daily_profit_amount ?? 'n/a'} · daily loss %: {ops[bot.id]?.daily_state?.daily_loss_pct ?? 'n/a'} · lock reason: {ops[bot.id]?.daily_state?.lock_reason || '—'}
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
                <button onClick={() => runAction(bot.id, 'reconcile')} className='px-3 py-2 rounded-lg bg-emerald-800 hover:bg-emerald-700 text-white text-sm font-medium'>
                  Reconcile now
                </button>
                <button onClick={() => runAction(bot.id, 'kill')} className='px-3 py-2 rounded-lg bg-red-800 hover:bg-red-700 text-white text-sm font-medium'>
                  Kill switch
                </button>
                <button onClick={() => runAction(bot.id, 'unkill')} className='px-3 py-2 rounded-lg bg-amber-700 hover:bg-amber-600 text-white text-sm font-medium'>
                  Reset kill
                </button>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

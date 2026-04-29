'use client';

import { useEffect, useMemo, useState } from 'react';

import { runtimeApi, type DailyState, type ReconciliationRun, type RuntimeStatus, type TradingIncident } from '@/lib/runtimeApi';

type Props = {
  workspaceId: string;
  botId: string;
};

type CheckRow = {
  key: string;
  label: string;
  ok: boolean;
  detail: string;
};

function Badge({ ok }: { ok: boolean }) {
  return (
    <span className={ok ? 'rounded-md border border-emerald-700 bg-emerald-950/60 px-2 py-0.5 text-xs text-emerald-300' : 'rounded-md border border-red-800 bg-red-950/60 px-2 py-0.5 text-xs text-red-300'}>
      {ok ? 'PASS' : 'BLOCK'}
    </span>
  );
}

export function LiveReadinessPanel({ workspaceId, botId }: Props) {
  const [runtime, setRuntime] = useState<RuntimeStatus | null>(null);
  const [daily, setDaily] = useState<DailyState | null>(null);
  const [incidents, setIncidents] = useState<TradingIncident[]>([]);
  const [runs, setRuns] = useState<ReconciliationRun[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      if (!workspaceId || !botId) return;
      setLoading(true);
      setError('');
      try {
        const [runtimeResp, dailyResp, incidentResp, runsResp] = await Promise.all([
          runtimeApi.getStatus(workspaceId, botId),
          runtimeApi.getDailyState(workspaceId, botId),
          runtimeApi.getIncidents(workspaceId, botId, 100),
          runtimeApi.getReconciliationRuns(workspaceId, botId, 20),
        ]);
        if (!mounted) return;
        setRuntime((runtimeResp.data || null) as RuntimeStatus | null);
        setDaily((dailyResp.data || null) as DailyState | null);
        setIncidents((incidentResp.data || []) as TradingIncident[]);
        setRuns((runsResp.data || []) as ReconciliationRun[]);
      } catch (err: unknown) {
        if (!mounted) return;
        const message = err instanceof Error ? err.message : 'Không tải được live readiness';
        setError(message);
      } finally {
        if (mounted) setLoading(false);
      }
    };
    load();
    return () => {
      mounted = false;
    };
  }, [workspaceId, botId]);

  const checks = useMemo<CheckRow[]>(() => {
    const runtimeStatus = String(runtime?.status || '').toLowerCase();
    const openCritical = incidents.filter((x) => String(x.status || '').toLowerCase() !== 'resolved' && String(x.severity || '').toLowerCase() === 'critical').length;
    const latestRun = runs[0];
    const reconOk = latestRun ? !['error', 'failed'].includes(String(latestRun.status || '').toLowerCase()) : false;

    return [
      {
        key: 'runtime',
        label: 'Runtime healthy',
        ok: ['running', 'healthy'].includes(runtimeStatus),
        detail: runtimeStatus || 'unavailable',
      },
      {
        key: 'daily_lock',
        label: 'Daily lock disabled',
        ok: !Boolean(daily?.locked),
        detail: daily?.locked ? String(daily.lock_reason || 'locked') : 'open',
      },
      {
        key: 'critical_incident',
        label: 'No open critical incident',
        ok: openCritical === 0,
        detail: openCritical === 0 ? '0 open' : `${openCritical} open`,
      },
      {
        key: 'reconciliation',
        label: 'Reconciliation healthy',
        ok: reconOk,
        detail: latestRun ? String(latestRun.status || 'unknown') : 'no-run',
      },
    ];
  }, [daily?.lock_reason, daily?.locked, incidents, runs, runtime?.status]);

  const ready = checks.every((c) => c.ok);

  return (
    <section className='rounded-xl border border-slate-800 bg-slate-950/40 p-4'>
      <div className='mb-3 flex items-center justify-between'>
        <h3 className='text-lg font-semibold'>Live Readiness</h3>
        <span className={ready ? 'rounded-md border border-emerald-700 bg-emerald-950/60 px-2 py-1 text-xs font-medium text-emerald-300' : 'rounded-md border border-red-800 bg-red-950/60 px-2 py-1 text-xs font-medium text-red-300'}>
          {ready ? 'READY' : 'BLOCKED'}
        </span>
      </div>

      {loading ? <p className='text-sm text-gray-400'>Đang kiểm tra readiness...</p> : null}
      {error ? <p className='text-sm text-red-300'>{error}</p> : null}

      {!loading && !error ? (
        <div className='space-y-2'>
          {checks.map((row) => (
            <div key={row.key} className='flex items-center justify-between rounded-lg border border-slate-800 bg-slate-900/50 px-3 py-2'>
              <div>
                <div className='text-sm text-white'>{row.label}</div>
                <div className='text-xs text-gray-500'>{row.detail}</div>
              </div>
              <Badge ok={row.ok} />
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

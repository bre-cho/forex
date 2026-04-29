'use client';

import { useEffect, useMemo, useState } from 'react';

import { runtimeApi, type ReconciliationRun, type TradingIncident } from '@/lib/runtimeApi';

type Props = {
  workspaceId: string;
  botId: string;
};

function isUnknownIncident(i: TradingIncident): boolean {
  const type = String(i.incident_type || '').toLowerCase();
  const title = String(i.title || '').toLowerCase();
  return type.includes('unknown_order') || title.includes('unknown order') || title.includes('reconcile');
}

export function UnknownOrdersPanel({ workspaceId, botId }: Props) {
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
        const [incidentResp, runsResp] = await Promise.all([
          runtimeApi.getIncidents(workspaceId, botId, 100),
          runtimeApi.getReconciliationRuns(workspaceId, botId, 20),
        ]);
        if (!mounted) return;
        setIncidents((incidentResp.data || []) as TradingIncident[]);
        setRuns((runsResp.data || []) as ReconciliationRun[]);
      } catch (err: unknown) {
        if (!mounted) return;
        const message = err instanceof Error ? err.message : 'Không tải được unknown orders';
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

  const openUnknownIncidents = useMemo(() => {
    return incidents
      .filter((i) => String(i.status || '').toLowerCase() !== 'resolved')
      .filter(isUnknownIncident);
  }, [incidents]);

  const recentFailedRuns = useMemo(() => {
    return runs.filter((r) => ['error', 'failed'].includes(String(r.status || '').toLowerCase())).slice(0, 5);
  }, [runs]);

  return (
    <section className='rounded-xl border border-slate-800 bg-slate-950/40 p-4'>
      <h3 className='mb-3 text-lg font-semibold'>Unknown Orders</h3>

      {loading ? <p className='text-sm text-gray-400'>Đang tải unknown orders...</p> : null}
      {error ? <p className='text-sm text-red-300'>{error}</p> : null}

      {!loading && !error ? (
        <div className='space-y-3'>
          <div className='rounded-lg border border-slate-800 bg-slate-900/50 p-3'>
            <div className='text-xs uppercase tracking-wide text-gray-500'>Open unknown incidents</div>
            <div className='mt-1 text-2xl font-semibold text-white'>{openUnknownIncidents.length}</div>
          </div>

          {openUnknownIncidents.length > 0 ? (
            <div className='space-y-2'>
              {openUnknownIncidents.slice(0, 5).map((item) => (
                <div key={item.id} className='rounded-lg border border-red-900/50 bg-red-950/20 p-3'>
                  <div className='text-sm font-medium text-red-200'>{item.title || item.incident_type || 'unknown_order'}</div>
                  <div className='mt-1 text-xs text-red-300/90'>{item.detail || 'Operator action required'}</div>
                  <div className='mt-1 text-xs text-red-400/80'>#{item.id} · {item.created_at || 'n/a'}</div>
                </div>
              ))}
            </div>
          ) : (
            <p className='text-sm text-emerald-300'>Không có unknown order incidents đang mở.</p>
          )}

          <div className='rounded-lg border border-slate-800 bg-slate-900/50 p-3'>
            <div className='text-xs uppercase tracking-wide text-gray-500'>Recent failed reconciliation runs</div>
            {recentFailedRuns.length === 0 ? (
              <p className='mt-1 text-sm text-emerald-300'>Không có run lỗi gần đây.</p>
            ) : (
              <ul className='mt-2 space-y-1 text-sm text-gray-200'>
                {recentFailedRuns.map((run) => (
                  <li key={run.id}>
                    run #{run.id} · status={run.status} · repaired={run.repaired ?? 0}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      ) : null}
    </section>
  );
}

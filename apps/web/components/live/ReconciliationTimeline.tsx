'use client';

import { useEffect, useState } from 'react';

import { runtimeApi, type ReconciliationRun } from '@/lib/runtimeApi';

type Props = {
  workspaceId: string;
  botId: string;
};

function statusClass(status: string): string {
  const v = String(status || '').toLowerCase();
  if (v === 'ok' || v === 'completed') return 'text-emerald-300 border-emerald-800 bg-emerald-950/40';
  if (v === 'error' || v === 'failed') return 'text-red-300 border-red-800 bg-red-950/40';
  return 'text-amber-300 border-amber-800 bg-amber-950/40';
}

export function ReconciliationTimeline({ workspaceId, botId }: Props) {
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
        const resp = await runtimeApi.getReconciliationRuns(workspaceId, botId, 30);
        if (!mounted) return;
        setRuns((resp.data || []) as ReconciliationRun[]);
      } catch (err: unknown) {
        if (!mounted) return;
        const message = err instanceof Error ? err.message : 'Không tải được reconciliation timeline';
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

  return (
    <section className='rounded-xl border border-slate-800 bg-slate-950/40 p-4'>
      <h3 className='mb-3 text-lg font-semibold'>Reconciliation Timeline</h3>
      {loading ? <p className='text-sm text-gray-400'>Đang tải reconciliation runs...</p> : null}
      {error ? <p className='text-sm text-red-300'>{error}</p> : null}

      {!loading && !error && runs.length === 0 ? <p className='text-sm text-gray-400'>Chưa có reconciliation run.</p> : null}

      {!loading && !error && runs.length > 0 ? (
        <div className='space-y-2'>
          {runs.slice(0, 10).map((run) => (
            <div key={run.id} className='rounded-lg border border-slate-800 bg-slate-900/50 p-3'>
              <div className='flex items-center justify-between'>
                <div className='text-sm font-medium text-white'>Run #{run.id}</div>
                <span className={`rounded-md border px-2 py-0.5 text-xs ${statusClass(run.status)}`}>{run.status}</span>
              </div>
              <div className='mt-2 text-xs text-gray-400'>
                broker={run.open_positions_broker ?? 'n/a'} · db={run.open_positions_db ?? 'n/a'} · repaired={run.repaired ?? 0}
              </div>
              <div className='mt-1 text-xs text-gray-500'>
                started={run.started_at || 'n/a'} · finished={run.finished_at || 'n/a'}
              </div>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

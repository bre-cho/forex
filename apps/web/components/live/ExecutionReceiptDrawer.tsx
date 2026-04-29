'use client';

import { useEffect, useMemo, useState } from 'react';

import { runtimeApi, type ExecutionReceipt } from '@/lib/runtimeApi';

type Props = {
  workspaceId: string;
  botId: string;
};

function shortHash(value?: string | null): string {
  const v = String(value || '');
  if (!v) return '—';
  if (v.length <= 12) return v;
  return `${v.slice(0, 8)}...${v.slice(-4)}`;
}

export function ExecutionReceiptDrawer({ workspaceId, botId }: Props) {
  const [receipts, setReceipts] = useState<ExecutionReceipt[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      if (!workspaceId || !botId) return;
      setLoading(true);
      setError('');
      try {
        const resp = await runtimeApi.getExecutionReceipts(workspaceId, botId, 30);
        if (!mounted) return;
        const rows = (resp.data || []) as ExecutionReceipt[];
        setReceipts(rows);
        if (rows.length > 0) setSelectedId(rows[0].id);
      } catch (err: unknown) {
        if (!mounted) return;
        const message = err instanceof Error ? err.message : 'Không tải được execution receipts';
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

  const selected = useMemo(() => receipts.find((r) => r.id === selectedId) || null, [receipts, selectedId]);

  return (
    <section className='rounded-xl border border-slate-800 bg-slate-950/40 p-4'>
      <h3 className='mb-3 text-lg font-semibold'>Execution Receipts</h3>
      {loading ? <p className='text-sm text-gray-400'>Đang tải execution receipts...</p> : null}
      {error ? <p className='text-sm text-red-300'>{error}</p> : null}

      {!loading && !error ? (
        <div className='grid grid-cols-1 gap-3 lg:grid-cols-2'>
          <div className='space-y-2'>
            {receipts.length === 0 ? <p className='text-sm text-gray-400'>Chưa có receipt.</p> : null}
            {receipts.map((row) => (
              <button
                type='button'
                key={row.id}
                onClick={() => setSelectedId(row.id)}
                className={`w-full rounded-lg border px-3 py-2 text-left ${selectedId === row.id ? 'border-blue-700 bg-blue-950/30' : 'border-slate-800 bg-slate-900/50'}`}
              >
                <div className='text-sm font-medium text-white'>{row.fill_status} · {row.submit_status}</div>
                <div className='text-xs text-gray-400'>
                  {row.broker} · order={row.broker_order_id || 'n/a'} · latency={Number(row.latency_ms || 0).toFixed(1)}ms
                </div>
                <div className='mt-1 text-xs text-gray-500'>hash={shortHash(row.raw_response_hash)}</div>
              </button>
            ))}
          </div>

          <div className='rounded-lg border border-slate-800 bg-slate-900/40 p-3'>
            {!selected ? <p className='text-sm text-gray-400'>Chọn receipt để xem chi tiết.</p> : null}
            {selected ? (
              <dl className='grid grid-cols-2 gap-2 text-sm'>
                <div>
                  <dt className='text-xs uppercase tracking-wide text-gray-500'>Idempotency</dt>
                  <dd className='break-all text-white'>{selected.idempotency_key}</dd>
                </div>
                <div>
                  <dt className='text-xs uppercase tracking-wide text-gray-500'>Client order</dt>
                  <dd className='break-all text-white'>{selected.client_order_id || '—'}</dd>
                </div>
                <div>
                  <dt className='text-xs uppercase tracking-wide text-gray-500'>Account</dt>
                  <dd className='text-white'>{selected.account_id || '—'}</dd>
                </div>
                <div>
                  <dt className='text-xs uppercase tracking-wide text-gray-500'>Server time</dt>
                  <dd className='text-white'>{selected.server_time ?? '—'}</dd>
                </div>
                <div>
                  <dt className='text-xs uppercase tracking-wide text-gray-500'>Volume</dt>
                  <dd className='text-white'>req={selected.requested_volume} · fill={selected.filled_volume}</dd>
                </div>
                <div>
                  <dt className='text-xs uppercase tracking-wide text-gray-500'>Price / fee</dt>
                  <dd className='text-white'>{selected.avg_fill_price ?? '—'} · {selected.commission ?? 0}</dd>
                </div>
                <div className='col-span-2'>
                  <dt className='text-xs uppercase tracking-wide text-gray-500'>Raw response hash</dt>
                  <dd className='break-all text-white'>{selected.raw_response_hash || '—'}</dd>
                </div>
              </dl>
            ) : null}
          </div>
        </div>
      ) : null}
    </section>
  );
}

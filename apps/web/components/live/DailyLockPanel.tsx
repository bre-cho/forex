'use client';

import { useEffect, useState } from 'react';

import { botApi } from '@/lib/api';
import { runtimeApi, type DailyState } from '@/lib/runtimeApi';

type Props = {
  workspaceId: string;
  botId: string;
};

export function DailyLockPanel({ workspaceId, botId }: Props) {
  const [state, setState] = useState<DailyState | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [error, setError] = useState('');

  const load = async () => {
    if (!workspaceId || !botId) return;
    setLoading(true);
    setError('');
    try {
      const resp = await runtimeApi.getDailyState(workspaceId, botId);
      setState((resp.data || null) as DailyState | null);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Không tải được daily lock state';
      setError(message);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspaceId, botId]);

  const resetLock = async () => {
    if (!workspaceId || !botId) return;
    setBusy(true);
    setError('');
    setMsg('');
    try {
      await botApi.resetDailyLock(workspaceId, botId, 'operator_panel_reset');
      setMsg('Đã reset daily lock.');
      await load();
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Reset daily lock thất bại';
      setError(message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className='rounded-xl border border-slate-800 bg-slate-950/40 p-4'>
      <div className='mb-3 flex items-center justify-between'>
        <h3 className='text-lg font-semibold'>Daily Lock</h3>
        <span className={state?.locked ? 'rounded-md border border-amber-700 bg-amber-950/60 px-2 py-1 text-xs text-amber-300' : 'rounded-md border border-emerald-700 bg-emerald-950/60 px-2 py-1 text-xs text-emerald-300'}>
          {state?.locked ? 'LOCKED' : 'OPEN'}
        </span>
      </div>

      {loading ? <p className='text-sm text-gray-400'>Đang tải daily state...</p> : null}
      {error ? <p className='mb-2 text-sm text-red-300'>{error}</p> : null}
      {msg ? <p className='mb-2 text-sm text-emerald-300'>{msg}</p> : null}

      {state ? (
        <dl className='grid grid-cols-2 gap-2 text-sm'>
          <div>
            <dt className='text-xs uppercase tracking-wide text-gray-500'>Trading day</dt>
            <dd className='text-white'>{state.trading_day || '—'}</dd>
          </div>
          <div>
            <dt className='text-xs uppercase tracking-wide text-gray-500'>Lock reason</dt>
            <dd className='text-white'>{state.lock_reason || '—'}</dd>
          </div>
          <div>
            <dt className='text-xs uppercase tracking-wide text-gray-500'>Daily pnl</dt>
            <dd className='text-white'>{Number(state.daily_profit_amount || 0).toFixed(2)}</dd>
          </div>
          <div>
            <dt className='text-xs uppercase tracking-wide text-gray-500'>Daily loss %</dt>
            <dd className='text-white'>{Number(state.daily_loss_pct || 0).toFixed(2)}%</dd>
          </div>
          <div>
            <dt className='text-xs uppercase tracking-wide text-gray-500'>Consecutive losses</dt>
            <dd className='text-white'>{state.consecutive_losses || 0}</dd>
          </div>
          <div>
            <dt className='text-xs uppercase tracking-wide text-gray-500'>Trades count</dt>
            <dd className='text-white'>{state.trades_count || 0}</dd>
          </div>
        </dl>
      ) : null}

      <div className='mt-4'>
        <button
          onClick={resetLock}
          disabled={!state?.locked || busy}
          className='rounded-lg bg-amber-800 px-3 py-2 text-sm font-medium text-white hover:bg-amber-700 disabled:opacity-50'
        >
          {busy ? 'Đang reset...' : 'Reset daily lock'}
        </button>
      </div>
    </section>
  );
}

'use client';

import { useMemo, useState } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

type OpsData = {
  runtime?: { status?: string; error_message?: string; metadata?: Record<string, unknown> };
  daily_state?: {
    daily_profit_amount?: number;
    daily_loss_pct?: number;
    locked?: boolean;
    lock_reason?: string;
  };
  open_incidents?: Array<{ id: number; severity?: string; title?: string; created_at?: string }>;
  latest_account_snapshot?: { equity?: number; balance?: number; free_margin?: number; currency?: string };
  latest_experiment?: { version?: number; stage?: string; updated_at?: string };
  latest_reconciliation?: { status?: string; mismatches?: unknown[]; repaired?: number };
};

type ParityResult = { mode: string; ok: boolean; reason: string; missing: string[] };

async function authedPost(path: string, body: unknown) {
  const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
  const res = await fetch(`${API_URL}${path}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

async function authedGet(path: string) {
  const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
  const res = await fetch(`${API_URL}${path}`, {
    headers: {
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
  if (!res.ok) throw new Error(await res.text());
  return res.json();
}

export default function OperationsDashboardPage() {
  const [workspaceId, setWorkspaceId] = useState('');
  const [botId, setBotId] = useState('');
  const [loading, setLoading] = useState(false);
  const [ops, setOps] = useState<OpsData | null>(null);
  const [parity, setParity] = useState<ParityResult[]>([]);
  const [error, setError] = useState('');

  const paritySummary = useMemo(() => {
    if (!parity.length) return '—';
    const okCount = parity.filter((x) => x.ok).length;
    return `${okCount}/${parity.length} mode đạt parity`;
  }, [parity]);

  const load = async () => {
    if (!workspaceId || !botId) {
      setError('Cần nhập workspace_id và bot_id');
      return;
    }
    setLoading(true);
    setError('');
    try {
      const opsData = (await authedGet(
        `/v1/workspaces/${workspaceId}/bots/${botId}/operations-dashboard`
      )) as OpsData;
      setOps(opsData);

      const parityData = await authedPost('/v1/qa/parity-contract/audit', {
        modes: ['backtest', 'paper', 'demo', 'live'],
        payload: {
          signal_id: 'qa-audit-sample',
          symbol: 'EURUSD',
          side: 'BUY',
          volume: 0.01,
          order_type: 'market',
          idempotency_key: 'qa-idem',
          brain_cycle_id: 'qa-cycle',
          pre_execution_context: { provider_mode: 'live' },
          success: true,
          submit_status: 'ACKED',
          fill_status: 'FILLED',
          broker_order_id: 'qa-broker-order',
        },
      });
      setParity((parityData?.results || []) as ParityResult[]);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  };

  return (
    <div>
      <h1 style={{ fontSize: '28px', fontWeight: 'bold', marginBottom: '16px' }}>
        Dashboard vận hành Live (QA/Admin)
      </h1>
      <p style={{ color: '#9ca3af', marginBottom: '20px' }}>
        Màn hình riêng cho vận hành tiếng Việt: runtime, rủi ro ngày, sự cố, snapshot tài khoản,
        stage thử nghiệm và kiểm tra parity contract theo mode.
      </p>

      <div style={{ display: 'flex', gap: 12, marginBottom: 16, flexWrap: 'wrap' }}>
        <input
          value={workspaceId}
          onChange={(e) => setWorkspaceId(e.target.value)}
          placeholder='workspace_id'
          style={{ background: '#111827', color: '#e5e7eb', border: '1px solid #374151', borderRadius: 8, padding: '10px 12px', minWidth: 240 }}
        />
        <input
          value={botId}
          onChange={(e) => setBotId(e.target.value)}
          placeholder='bot_id'
          style={{ background: '#111827', color: '#e5e7eb', border: '1px solid #374151', borderRadius: 8, padding: '10px 12px', minWidth: 240 }}
        />
        <button
          onClick={load}
          disabled={loading}
          style={{ background: '#0f766e', color: 'white', border: 'none', borderRadius: 8, padding: '10px 14px', fontWeight: 600 }}
        >
          {loading ? 'Đang tải...' : 'Tải dashboard'}
        </button>
      </div>

      {error ? (
        <div style={{ background: '#3f1d1d', color: '#fecaca', border: '1px solid #7f1d1d', borderRadius: 8, padding: 12, marginBottom: 14 }}>
          {error}
        </div>
      ) : null}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, minmax(0,1fr))', gap: 12, marginBottom: 16 }}>
        <Card title='Runtime status' value={String(ops?.runtime?.status || '—')} />
        <Card title='Daily lock' value={ops?.daily_state?.locked ? 'ĐANG KHÓA' : 'Không khóa'} />
        <Card title='Parity summary' value={paritySummary} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, minmax(0,1fr))', gap: 12 }}>
        <Panel title='Tài khoản & rủi ro ngày'>
          <Line k='Equity' v={ops?.latest_account_snapshot?.equity} />
          <Line k='Balance' v={ops?.latest_account_snapshot?.balance} />
          <Line k='Free margin' v={ops?.latest_account_snapshot?.free_margin} />
          <Line k='Currency' v={ops?.latest_account_snapshot?.currency} />
          <Line k='Daily PnL' v={ops?.daily_state?.daily_profit_amount} />
          <Line k='Daily loss %' v={ops?.daily_state?.daily_loss_pct} />
          <Line k='Lock reason' v={ops?.daily_state?.lock_reason} />
        </Panel>

        <Panel title='Thử nghiệm & reconciliation'>
          <Line k='Experiment stage' v={ops?.latest_experiment?.stage} />
          <Line k='Experiment version' v={ops?.latest_experiment?.version} />
          <Line k='Experiment updated' v={ops?.latest_experiment?.updated_at} />
          <Line k='Reconciliation status' v={ops?.latest_reconciliation?.status} />
          <Line k='Repaired count' v={ops?.latest_reconciliation?.repaired} />
        </Panel>

        <Panel title='Sự cố đang mở'>
          {(ops?.open_incidents || []).length === 0 ? (
            <div style={{ color: '#9ca3af' }}>Không có sự cố mở</div>
          ) : (
            (ops?.open_incidents || []).slice(0, 10).map((it) => (
              <div key={it.id} style={{ padding: '8px 0', borderBottom: '1px solid #1f2937' }}>
                <div style={{ fontWeight: 600 }}>{it.title || `Incident #${it.id}`}</div>
                <div style={{ color: '#9ca3af', fontSize: 13 }}>severity: {it.severity || 'n/a'}</div>
              </div>
            ))
          )}
        </Panel>

        <Panel title='Kết quả parity contract theo mode'>
          {parity.length === 0 ? (
            <div style={{ color: '#9ca3af' }}>Chưa chạy audit parity</div>
          ) : (
            parity.map((p) => (
              <div key={p.mode} style={{ padding: '8px 0', borderBottom: '1px solid #1f2937' }}>
                <div style={{ fontWeight: 600 }}>
                  {p.mode.toUpperCase()} · {p.ok ? 'OK' : 'FAIL'}
                </div>
                <div style={{ color: '#9ca3af', fontSize: 13 }}>reason: {p.reason}</div>
                {!p.ok && p.missing?.length ? (
                  <div style={{ color: '#fca5a5', fontSize: 12 }}>missing: {p.missing.join(', ')}</div>
                ) : null}
              </div>
            ))
          )}
        </Panel>
      </div>
    </div>
  );
}

function Card({ title, value }: { title: string; value: string }) {
  return (
    <div style={{ background: '#111827', border: '1px solid #1f2937', borderRadius: 12, padding: 14 }}>
      <div style={{ color: '#9ca3af', fontSize: 13 }}>{title}</div>
      <div style={{ fontSize: 22, fontWeight: 700, marginTop: 8 }}>{value}</div>
    </div>
  );
}

function Panel({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ background: '#111827', border: '1px solid #1f2937', borderRadius: 12, padding: 14 }}>
      <div style={{ fontWeight: 700, marginBottom: 8 }}>{title}</div>
      <div>{children}</div>
    </div>
  );
}

function Line({ k, v }: { k: string; v: unknown }) {
  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', borderBottom: '1px solid #1f2937', padding: '8px 0' }}>
      <span style={{ color: '#9ca3af' }}>{k}</span>
      <span>{v === undefined || v === null || v === '' ? '—' : String(v)}</span>
    </div>
  );
}

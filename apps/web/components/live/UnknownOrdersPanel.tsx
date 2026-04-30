'use client';

import { useEffect, useMemo, useState } from 'react';

import {
  runtimeApi,
  type ReconciliationAttemptEvent,
  type ReconciliationQueueItem,
  type ReconciliationRun,
  type TradingIncident,
} from '@/lib/runtimeApi';

type Props = {
  workspaceId: string;
  botId: string;
};

function isUnknownIncident(i: TradingIncident): boolean {
  const type = String(i.incident_type || '').toLowerCase();
  const title = String(i.title || '').toLowerCase();
  return type.includes('unknown_order') || title.includes('unknown order') || title.includes('reconcile');
}

function resolutionSeverity(code: string | null | undefined): 'critical' | 'warning' | 'success' | 'info' {
  const normalized = String(code || '').toLowerCase();
  if (!normalized) return 'info';
  if (
    normalized.includes('error') ||
    normalized.includes('failed') ||
    normalized.includes('exception') ||
    normalized.includes('deadline') ||
    normalized.includes('max_attempts') ||
    normalized.includes('persist') ||
    normalized.includes('dead_letter')
  ) {
    return 'critical';
  }
  if (normalized.includes('filled') || normalized.includes('rejected') || normalized.includes('resolved')) {
    return 'success';
  }
  if (
    normalized.includes('pending') ||
    normalized.includes('partial') ||
    normalized.includes('not_found') ||
    normalized.includes('ambiguous')
  ) {
    return 'warning';
  }
  return 'info';
}

function severityBadgeClass(level: 'critical' | 'warning' | 'success' | 'info'): string {
  if (level === 'critical') return 'border-red-700/70 bg-red-950/40 text-red-200';
  if (level === 'warning') return 'border-amber-700/70 bg-amber-950/40 text-amber-200';
  if (level === 'success') return 'border-emerald-700/70 bg-emerald-950/40 text-emerald-200';
  return 'border-slate-600/70 bg-slate-900/70 text-slate-200';
}

export function UnknownOrdersPanel({ workspaceId, botId }: Props) {
  const [incidents, setIncidents] = useState<TradingIncident[]>([]);
  const [runs, setRuns] = useState<ReconciliationRun[]>([]);
  const [queueItems, setQueueItems] = useState<ReconciliationQueueItem[]>([]);
  const [attemptEvents, setAttemptEvents] = useState<ReconciliationAttemptEvent[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [eventsLoading, setEventsLoading] = useState(false);

  const [selectedQueueItemId, setSelectedQueueItemId] = useState<number | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [outcome, setOutcome] = useState<'filled' | 'rejected'>('filled');
  const [provider, setProvider] = useState('');
  const [evidenceRef, setEvidenceRef] = useState('');
  const [observedAt, setObservedAt] = useState('');
  const [payloadHash, setPayloadHash] = useState('');
  const [rawResponseHash, setRawResponseHash] = useState('');
  const [brokerOrderId, setBrokerOrderId] = useState('');
  const [brokerDealId, setBrokerDealId] = useState('');
  const [brokerPositionId, setBrokerPositionId] = useState('');
  const [formErrors, setFormErrors] = useState<string[]>([]);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [queueStatusFilter, setQueueStatusFilter] = useState<'all' | 'pending' | 'retry' | 'failed_needs_operator' | 'dead_letter'>('all');
  const [attemptOutcomeFilter, setAttemptOutcomeFilter] = useState<string>('all');
  const [idempotencyKeyFilter, setIdempotencyKeyFilter] = useState<string>('');
  const [copiedField, setCopiedField] = useState<string | null>(null);

  const resetForm = () => {
    setOutcome('filled');
    setProvider('');
    setEvidenceRef('');
    setObservedAt('');
    setPayloadHash('');
    setRawResponseHash('');
    setBrokerOrderId('');
    setBrokerDealId('');
    setBrokerPositionId('');
    setFormErrors([]);
  };

  useEffect(() => {
    let mounted = true;
    const load = async () => {
      if (!workspaceId || !botId) return;
      setLoading(true);
      setError('');
      setSuccess('');
      try {
        const [incidentResp, runsResp, queueResp] = await Promise.all([
          runtimeApi.getIncidents(workspaceId, botId, 100),
          runtimeApi.getReconciliationRuns(workspaceId, botId, 20),
          runtimeApi.getReconciliationQueueItems(workspaceId, botId, {
            statuses: ['failed_needs_operator', 'dead_letter', 'pending', 'retry'],
            limit: 100,
          }),
        ]);
        if (!mounted) return;
        setIncidents((incidentResp.data || []) as TradingIncident[]);
        setRuns((runsResp.data || []) as ReconciliationRun[]);
        setQueueItems((queueResp.data || []) as ReconciliationQueueItem[]);
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

  const unresolvedQueueItems = useMemo(() => {
    return queueItems.filter((item) => ['pending', 'retry', 'failed_needs_operator', 'dead_letter'].includes(String(item.status || '').toLowerCase()));
  }, [queueItems]);

  const displayedQueueItems = useMemo(() => {
    let items = queueStatusFilter === 'all' ? unresolvedQueueItems : unresolvedQueueItems.filter((item) => String(item.status || '').toLowerCase() === queueStatusFilter);
    const keyTrim = idempotencyKeyFilter.trim().toLowerCase();
    if (keyTrim) items = items.filter((item) => String(item.idempotency_key || '').toLowerCase().includes(keyTrim));
    return items;
  }, [unresolvedQueueItems, queueStatusFilter, idempotencyKeyFilter]);

  const selectedQueueItem = useMemo(() => {
    if (selectedQueueItemId == null) return null;
    return queueItems.find((item) => Number(item.id) === Number(selectedQueueItemId)) || null;
  }, [queueItems, selectedQueueItemId]);

  const attemptOutcomeOptions = useMemo(() => {
    const values = new Set<string>();
    for (const event of attemptEvents) {
      const normalized = String(event.outcome || '').trim().toLowerCase();
      if (normalized) values.add(normalized);
    }
    return ['all', ...Array.from(values)];
  }, [attemptEvents]);

  const displayedAttemptEvents = useMemo(() => {
    if (attemptOutcomeFilter === 'all') return attemptEvents;
    return attemptEvents.filter(
      (event) => String(event.outcome || '').trim().toLowerCase() === String(attemptOutcomeFilter).toLowerCase()
    );
  }, [attemptEvents, attemptOutcomeFilter]);

  const validateForm = (): string[] => {
    const errors: string[] = [];
    if (!provider.trim()) errors.push('Provider là bắt buộc.');
    if (!evidenceRef.trim()) errors.push('Evidence ref là bắt buộc.');
    if (!observedAt.trim()) {
      errors.push('Observed at là bắt buộc.');
    } else {
      const parsed = Date.parse(observedAt.trim());
      if (Number.isNaN(parsed)) {
        errors.push('Observed at phải là thời gian hợp lệ (ISO 8601).');
      }
    }
    if (!payloadHash.trim() && !rawResponseHash.trim()) {
      errors.push('Cần payload hash hoặc raw response hash.');
    }
    if (outcome === 'filled' && !brokerOrderId.trim() && !brokerDealId.trim() && !brokerPositionId.trim()) {
      errors.push('Outcome filled cần ít nhất một định danh broker: order/deal/position id.');
    }
    return errors;
  };

  const submitManualResolve = async () => {
    if (!workspaceId || !botId || selectedQueueItemId == null) return;
    setError('');
    setSuccess('');

    const errors = validateForm();
    setFormErrors(errors);
    if (errors.length > 0) {
      setConfirmOpen(false);
      return;
    }

    setConfirmOpen(true);
  };

  const confirmManualResolve = async () => {
    if (!workspaceId || !botId || selectedQueueItemId == null) return;
    setError('');
    setSuccess('');
    setConfirmOpen(false);

    setSubmitting(true);
    try {
      await runtimeApi.resolveReconciliationItem(workspaceId, botId, selectedQueueItemId, {
        outcome,
        broker_proof: {
          provider: provider.trim(),
          evidence_ref: evidenceRef.trim(),
          observed_at: observedAt.trim(),
          payload_hash: payloadHash.trim() || undefined,
          raw_response_hash: rawResponseHash.trim() || undefined,
          broker_order_id: brokerOrderId.trim() || undefined,
          broker_deal_id: brokerDealId.trim() || undefined,
          broker_position_id: brokerPositionId.trim() || undefined,
        },
      });
      setSuccess('Đã resolve reconciliation item thành công.');
      setSelectedQueueItemId(null);
      setAttemptEvents([]);
      resetForm();
      const queueResp = await runtimeApi.getReconciliationQueueItems(workspaceId, botId, {
        statuses: ['failed_needs_operator', 'dead_letter', 'pending', 'retry', 'resolved'],
        limit: 100,
      });
      setQueueItems((queueResp.data || []) as ReconciliationQueueItem[]);
    } catch (err: unknown) {
      const message = err instanceof Error ? err.message : 'Resolve thất bại';
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  useEffect(() => {
    let mounted = true;
    const loadAttemptEvents = async () => {
      if (!workspaceId || !botId || selectedQueueItemId == null) {
        setAttemptEvents([]);
        return;
      }
      setEventsLoading(true);
      try {
        const resp = await runtimeApi.getReconciliationAttemptEvents(
          workspaceId,
          botId,
          selectedQueueItemId,
          100
        );
        if (!mounted) return;
        setAttemptEvents((resp.data || []) as ReconciliationAttemptEvent[]);
      } catch (err: unknown) {
        if (!mounted) return;
        setAttemptEvents([]);
      } finally {
        if (mounted) setEventsLoading(false);
      }
    };
    loadAttemptEvents();
    return () => {
      mounted = false;
    };
  }, [workspaceId, botId, selectedQueueItemId]);

  return (
    <section className='rounded-xl border border-slate-800 bg-slate-950/40 p-4'>
      <h3 className='mb-3 text-lg font-semibold'>Unknown Orders</h3>

      {loading ? <p className='text-sm text-gray-400'>Đang tải unknown orders...</p> : null}
      {error ? <p className='text-sm text-red-300'>{error}</p> : null}
      {success ? <p className='text-sm text-emerald-300'>{success}</p> : null}

      {!loading && !error ? (
        <div className='space-y-3'>
          <div className='rounded-lg border border-slate-800 bg-slate-900/50 p-3'>
            <div className='text-xs uppercase tracking-wide text-gray-500'>Open unknown incidents</div>
            <div className='mt-1 text-2xl font-semibold text-white'>{openUnknownIncidents.length}</div>
          </div>

          <div className='rounded-lg border border-slate-800 bg-slate-900/50 p-3'>
            <div className='text-xs uppercase tracking-wide text-gray-500'>Pending queue items cần xử lý</div>
            <div className='mt-1 text-2xl font-semibold text-white'>{unresolvedQueueItems.length}</div>
            <div className='mt-2 flex flex-wrap items-center gap-2'>
              <label className='text-xs text-slate-400'>
                Lọc status
                <select
                  className='ml-2 rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-white'
                  value={queueStatusFilter}
                  onChange={(e) => setQueueStatusFilter(e.target.value as 'all' | 'pending' | 'retry' | 'failed_needs_operator' | 'dead_letter')}
                >
                  <option value='all'>all</option>
                  <option value='pending'>pending</option>
                  <option value='retry'>retry</option>
                  <option value='failed_needs_operator'>failed_needs_operator</option>
                  <option value='dead_letter'>dead_letter</option>
                </select>
              </label>
              <span className='text-xs text-slate-500'>Hiển thị: {displayedQueueItems.length}</span>
            </div>
            <div className='mt-2'>
              <input
                type='text'
                className='w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-white placeholder:text-slate-500'
                placeholder='Tìm theo idempotency_key...'
                value={idempotencyKeyFilter}
                onChange={(e) => setIdempotencyKeyFilter(e.target.value)}
              />
            </div>
            {displayedQueueItems.length > 0 ? (
              <div className='mt-3 space-y-2'>
                {displayedQueueItems.slice(0, 8).map((item) => {
                  const active = Number(selectedQueueItemId) === Number(item.id);
                  return (
                    <div key={item.id} className='rounded-lg border border-slate-700 bg-slate-800/40 p-3'>
                      <div className='flex flex-wrap items-center justify-between gap-2'>
                        <div>
                          <div className='text-sm text-white'>queue #{item.id} · {item.status}</div>
                          <div className='text-xs text-slate-300'>idempotency: {item.idempotency_key}</div>
                          <div className='text-xs text-slate-400'>attempts: {item.attempts ?? 0}/{item.max_attempts ?? 0}</div>
                        </div>
                        <button
                          type='button'
                          className={`rounded-md px-3 py-1 text-xs font-medium ${active ? 'bg-red-700 text-white' : 'bg-slate-700 text-slate-100 hover:bg-slate-600'}`}
                          onClick={() => {
                            setSelectedQueueItemId(active ? null : Number(item.id));
                            setFormErrors([]);
                            setAttemptOutcomeFilter('all');
                            setConfirmOpen(false);
                          }}
                        >
                          {active ? 'Đóng form' : 'Manual Resolve'}
                        </button>
                      </div>
                      {item.last_error ? (
                        <div className='mt-2 text-xs text-amber-300'>last_error: {item.last_error}</div>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            ) : (
              <p className='mt-1 text-sm text-emerald-300'>Không có queue item cần operator xử lý.</p>
            )}
          </div>

          {selectedQueueItem ? (
            <div className='rounded-lg border border-blue-900/60 bg-blue-950/20 p-3'>
              <div className='mb-2 text-sm font-semibold text-blue-200'>Manual Resolve Queue #{selectedQueueItem.id}</div>
              <div className='mb-3 text-xs text-blue-300/90'>
                Idempotency: {selectedQueueItem.idempotency_key} · Status: {selectedQueueItem.status}
              </div>

              <div className='mb-3 rounded-md border border-slate-700 bg-slate-900/60 p-2'>
                <div className='text-xs uppercase tracking-wide text-slate-400'>Reconciliation Attempt Timeline</div>
                  <div className='mt-2 flex flex-wrap items-center gap-2'>
                    <label className='text-xs text-slate-400'>
                      Lọc outcome
                      <select
                        className='ml-2 rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-white'
                        value={attemptOutcomeFilter}
                        onChange={(e) => setAttemptOutcomeFilter(e.target.value)}
                      >
                        {attemptOutcomeOptions.map((opt) => (
                          <option key={opt} value={opt}>{opt}</option>
                        ))}
                      </select>
                    </label>
                    <span className='text-xs text-slate-500'>Hiển thị: {displayedAttemptEvents.length}</span>
                  </div>
                  {eventsLoading ? <p className='mt-1 text-xs text-slate-400'>Đang tải attempt events...</p> : null}
                  {!eventsLoading && attemptEvents.length === 0 ? (
                    <p className='mt-1 text-xs text-slate-400'>Chưa có attempt events cho queue item này.</p>
                  ) : null}
                  {!eventsLoading && displayedAttemptEvents.length > 0 ? (
                    <ul className='mt-2 space-y-1 text-xs text-slate-200'>
                      {displayedAttemptEvents.slice(0, 8).map((event) => {
                        const severity = resolutionSeverity(event.resolution_code);
                        return (
                          <li key={event.id} className='rounded border border-slate-700 bg-slate-950/40 p-2'>
                            <div className='flex flex-wrap items-center gap-2'>
                              <span>#{event.id} · attempt {event.attempt_no}</span>
                              <span className='rounded border border-slate-600/70 bg-slate-900/70 px-2 py-0.5 text-[11px] text-slate-200'>
                                outcome={event.outcome}
                              </span>
                              <span className={`rounded border px-2 py-0.5 text-[11px] ${severityBadgeClass(severity)}`}>
                                {severity.toUpperCase()} · {event.resolution_code || 'n/a'}
                              </span>
                            </div>
                            <div className='text-slate-400'>
                              provider={event.provider || 'n/a'} · worker={event.worker_id || 'n/a'} · {event.created_at || 'n/a'}
                            </div>
                            <div className='flex flex-wrap items-center gap-2 text-slate-500'>
                              <span>payload_hash={event.payload_hash ? `${event.payload_hash.slice(0, 12)}…` : 'n/a'}</span>
                              {event.payload_hash ? (
                                <button
                                  type='button'
                                  className='rounded border border-slate-700 bg-slate-800 px-1.5 py-0.5 text-[10px] text-slate-300 hover:bg-slate-700'
                                  title='Sao chép payload_hash'
                                  onClick={() => { navigator.clipboard.writeText(event.payload_hash!); setCopiedField(`ph-${event.id}`); setTimeout(() => setCopiedField(null), 2000); }}
                                >
                                  {copiedField === `ph-${event.id}` ? '✓ Đã sao chép' : 'Copy hash'}
                                </button>
                              ) : null}
                            </div>
                          </li>
                        );
                      })}
                    </ul>
                  ) : null}
              </div>

              <div className='grid gap-2 md:grid-cols-2'>
                <label className='text-xs text-slate-300'>
                  Outcome
                  <select
                    className='mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-white'
                    value={outcome}
                    onChange={(e) => setOutcome(e.target.value as 'filled' | 'rejected')}
                  >
                    <option value='filled'>filled</option>
                    <option value='rejected'>rejected</option>
                  </select>
                </label>

                <label className='text-xs text-slate-300'>
                  Provider *
                  <input
                    className='mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-white'
                    value={provider}
                    onChange={(e) => setProvider(e.target.value)}
                    placeholder='ctrader / mt5 / bybit'
                  />
                </label>

                <label className='text-xs text-slate-300'>
                  Evidence Ref *
                  <input
                    className='mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-white'
                    value={evidenceRef}
                    onChange={(e) => setEvidenceRef(e.target.value)}
                    placeholder='ticket-123, screenshot-id...'
                  />
                </label>

                <label className='text-xs text-slate-300'>
                  Observed At (ISO) *
                  <input
                    className='mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-white'
                    value={observedAt}
                    onChange={(e) => setObservedAt(e.target.value)}
                    placeholder='2026-04-30T10:10:10Z'
                  />
                </label>

                <label className='text-xs text-slate-300'>
                  Payload Hash
                  <input
                    className='mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-white'
                    value={payloadHash}
                    onChange={(e) => setPayloadHash(e.target.value)}
                    placeholder='sha256...'
                  />
                </label>

                <label className='text-xs text-slate-300'>
                  Raw Response Hash
                  <input
                    className='mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-white'
                    value={rawResponseHash}
                    onChange={(e) => setRawResponseHash(e.target.value)}
                    placeholder='sha256...'
                  />
                </label>

                <label className='text-xs text-slate-300'>
                  Broker Order ID
                  <input
                    className='mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-white'
                    value={brokerOrderId}
                    onChange={(e) => setBrokerOrderId(e.target.value)}
                    placeholder='ord-...'
                  />
                </label>

                <label className='text-xs text-slate-300'>
                  Broker Deal ID
                  <input
                    className='mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-white'
                    value={brokerDealId}
                    onChange={(e) => setBrokerDealId(e.target.value)}
                    placeholder='deal-...'
                  />
                </label>

                <label className='text-xs text-slate-300'>
                  Broker Position ID
                  <input
                    className='mt-1 w-full rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-sm text-white'
                    value={brokerPositionId}
                    onChange={(e) => setBrokerPositionId(e.target.value)}
                    placeholder='pos-...'
                  />
                </label>
              </div>

              {formErrors.length > 0 ? (
                <ul className='mt-3 space-y-1 text-xs text-red-300'>
                  {formErrors.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              ) : null}

              <div className='mt-3 flex gap-2'>
                <button
                  type='button'
                  disabled={submitting}
                  className='rounded-md bg-blue-700 px-3 py-1 text-sm font-medium text-white hover:bg-blue-600 disabled:opacity-60'
                  onClick={submitManualResolve}
                >
                  {submitting ? 'Đang gửi...' : 'Tiếp tục đến bước xác nhận'}
                </button>
                <button
                  type='button'
                  disabled={submitting}
                  className='rounded-md bg-slate-700 px-3 py-1 text-sm font-medium text-slate-100 hover:bg-slate-600 disabled:opacity-60'
                  onClick={() => {
                    setSelectedQueueItemId(null);
                    resetForm();
                  }}
                >
                  Hủy
                </button>
              </div>
            </div>
          ) : null}

          {confirmOpen && selectedQueueItem ? (
            <div className='fixed inset-0 z-40 flex items-center justify-center bg-black/70 p-4'>
              <div className='w-full max-w-lg rounded-lg border border-amber-700/50 bg-slate-900 p-4'>
                <div className='text-lg font-semibold text-amber-200'>Xác nhận manual resolve lần cuối</div>
                <p className='mt-2 text-sm text-slate-200'>
                  Bạn sắp resolve queue #{selectedQueueItem.id} với outcome <span className='font-semibold text-white'>{outcome}</span>.
                </p>
                <p className='mt-1 text-xs text-slate-400'>
                  Hành động này sẽ ghi lifecycle event và đánh dấu reconciliation queue item là resolved.
                </p>
                <div className='mt-3 rounded-md border border-slate-700 bg-slate-950/60 p-2 text-xs text-slate-300'>
                  <div>provider: {provider || 'n/a'}</div>
                  <div>evidence_ref: {evidenceRef || 'n/a'}</div>
                  <div>observed_at: {observedAt || 'n/a'}</div>
                  <div>hash: {payloadHash || rawResponseHash || 'n/a'}</div>
                  <div>
                    broker ids: {[brokerOrderId, brokerDealId, brokerPositionId].filter(Boolean).join(', ') || 'n/a'}
                  </div>
                </div>
                <div className='mt-4 flex gap-2'>
                  <button
                    type='button'
                    className='rounded-md bg-amber-700 px-3 py-1 text-sm font-medium text-white hover:bg-amber-600 disabled:opacity-60'
                    disabled={submitting}
                    onClick={confirmManualResolve}
                  >
                    {submitting ? 'Đang gửi...' : 'Xác nhận và Submit'}
                  </button>
                  <button
                    type='button'
                    className='rounded-md bg-slate-700 px-3 py-1 text-sm font-medium text-slate-100 hover:bg-slate-600'
                    onClick={() => setConfirmOpen(false)}
                    disabled={submitting}
                  >
                    Quay lại
                  </button>
                </div>
              </div>
            </div>
          ) : null}

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

'use client';
import { useEffect, useState } from 'react';
import { useParams } from 'next/navigation';
import {
  useBot,
  useBotRuntime,
  useBotActions,
  useBotDailyState,
  useBotIncidents,
  useBotLiveActions,
} from '@/hooks/useBots';
import { useBotWebSocket } from '@/hooks/useWebSocket';
import { workspaceApi } from '@/lib/api';

function toVietnameseStatus(status?: string): string {
  const map: Record<string, string> = {
    running: 'đang_chạy',
    stopped: 'đã_dừng',
    paused: 'tạm_dừng',
    error: 'lỗi',
    starting: 'đang_khởi_động',
    healthy: 'ổn_định',
    degraded: 'suy_giảm',
    disconnected: 'mất_kết_nối',
    connected: 'đã_kết_nối',
    not_running: 'chưa_chạy',
  };
  if (!status) return 'không_rõ';
  return map[String(status).toLowerCase()] ?? String(status);
}

function toVietnameseRuntimeKey(key: string): string {
  const map: Record<string, string> = {
    status: 'trạng_thái',
    started_at: 'thời_điểm_bắt_đầu',
    stopped_at: 'thời_điểm_dừng',
    error_message: 'thông_báo_lỗi',
    open_trades: 'lệnh_mở',
    total_trades: 'tổng_lệnh',
    balance: 'số_dư',
    equity: 'vốn_chủ_sở_hữu',
    metadata: 'siêu_dữ_liệu',
  };
  return map[key] ?? key;
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    running: 'bg-green-900 text-green-300',
    stopped: 'bg-gray-700 text-gray-300',
    paused: 'bg-yellow-900 text-yellow-300',
    error: 'bg-red-900 text-red-300',
  };
  return (
    <span
      className={`px-2 py-0.5 rounded text-xs font-medium ${
        colors[status] ?? 'bg-gray-700 text-gray-300'
      }`}
    >
      {toVietnameseStatus(status)}
    </span>
  );
}

function severityBadgeClass(severity?: string): string {
  switch (String(severity).toLowerCase()) {
    case 'critical':
      return 'bg-red-950 text-red-300 border border-red-800';
    case 'warning':
      return 'bg-amber-950 text-amber-300 border border-amber-800';
    default:
      return 'bg-slate-800 text-slate-300 border border-slate-700';
  }
}

export default function BotDetailPage() {
  const params = useParams<{ id: string }>();
  const botId = params.id;
  const [workspaceId, setWorkspaceId] = useState<string>('');
  const [wsLoading, setWsLoading] = useState(true);

  // Resolve workspace from the first available workspace
  useEffect(() => {
    workspaceApi.list().then((r) => {
      const ws = r.data as { id: string }[];
      if (ws?.length) setWorkspaceId(ws[0].id);
    }).finally(() => setWsLoading(false));
  }, []);

  const { data: bot, isLoading: botLoading, error: botError } = useBot(workspaceId, botId);
  const { data: runtime, isLoading: runtimeLoading } = useBotRuntime(workspaceId, botId);
  const { data: dailyState, isLoading: dailyStateLoading } = useBotDailyState(workspaceId, botId);
  const { data: incidents, isLoading: incidentsLoading } = useBotIncidents(workspaceId, botId);
  const { startBot, stopBot } = useBotActions(workspaceId);
  const { reconcileNow, resetDailyLock, resolveIncident } = useBotLiveActions(workspaceId, botId);
  const { data: wsData, isConnected } = useBotWebSocket(botId);
  const [actionMessage, setActionMessage] = useState<string>('');
  const [actionError, setActionError] = useState<string>('');

  const loading = wsLoading || botLoading;

  if (loading) {
    return (
      <div>
        <h1 className="text-3xl font-bold mb-6">Bot</h1>
        <div className="animate-pulse space-y-4">
          <div className="h-8 bg-surface-muted rounded w-48" />
          <div className="h-32 bg-surface-muted rounded" />
        </div>
      </div>
    );
  }

  if (botError || !bot) {
    return (
      <div>
        <h1 className="text-3xl font-bold mb-6">Bot: {botId}</h1>
        <div className="bg-red-900/40 border border-red-700 rounded-xl p-4 text-red-300">
          Không tìm thấy bot hoặc bạn không có quyền truy cập.
        </div>
      </div>
    );
  }

  const isRunning = bot.status === 'running';
  const isLive = bot.mode === 'live';
  const dailyLocked = Boolean(dailyState?.locked);

  const runReconcileNow = async () => {
    setActionError('');
    try {
      const result = await reconcileNow.mutateAsync();
      setActionMessage(`Đã reconcile: ${String(result.status ?? 'ok')}`);
    } catch (error: any) {
      setActionMessage('');
      setActionError(String(error?.response?.data?.detail ?? error?.message ?? 'Reconcile thất bại'));
    }
  };

  const runResetDailyLock = async () => {
    setActionError('');
    try {
      await resetDailyLock.mutateAsync();
      setActionMessage('Đã mở khóa ngày giao dịch.');
    } catch (error: any) {
      setActionMessage('');
      setActionError(String(error?.response?.data?.detail ?? error?.message ?? 'Reset lock thất bại'));
    }
  };

  const runResolveIncident = async (incidentId: number) => {
    setActionError('');
    try {
      await resolveIncident.mutateAsync(incidentId);
      setActionMessage(`Đã resolve incident #${incidentId}.`);
    } catch (error: any) {
      setActionMessage('');
      setActionError(String(error?.response?.data?.detail ?? error?.message ?? 'Resolve incident thất bại'));
    }
  };

  return (
    <div>
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div>
          <h1 className="text-3xl font-bold">{bot.name}</h1>
          <div className="flex items-center gap-3 mt-1">
            <StatusBadge status={bot.status} />
            <span className="text-sm text-gray-400">
              {bot.symbol} · {bot.timeframe} · {bot.mode}
            </span>
          </div>
        </div>
        <div className="flex gap-2">
          {!isRunning ? (
            <button
              onClick={() => startBot.mutate(botId)}
              disabled={startBot.isPending}
              className="px-4 py-2 bg-green-700 hover:bg-green-600 text-white rounded-lg text-sm font-medium disabled:opacity-50"
            >
              {startBot.isPending ? 'Đang khởi động…' : '▶ Bắt đầu'}
            </button>
          ) : (
            <button
              onClick={() => stopBot.mutate(botId)}
              disabled={stopBot.isPending}
              className="px-4 py-2 bg-red-700 hover:bg-red-600 text-white rounded-lg text-sm font-medium disabled:opacity-50"
            >
              {stopBot.isPending ? 'Đang dừng…' : '■ Dừng'}
            </button>
          )}
        </div>
      </div>

      {/* WS connection indicator */}
      <div className="flex items-center gap-2 mb-6">
        <div className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-400' : 'bg-gray-500'}`} />
        <span className="text-xs text-gray-400">
          {isConnected ? 'WebSocket đã kết nối — đang nhận cập nhật trực tiếp' : 'WebSocket đã ngắt kết nối'}
        </span>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {isLive ? (
          <div className="bg-surface-muted rounded-xl p-4 md:col-span-2 border border-slate-800">
            <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
              <div>
                <h3 className="text-lg font-semibold mb-1">Live Control Center</h3>
                <p className="text-sm text-gray-400">
                  Điều hành reconcile, xử lý incident và mở khóa ngày ngay trên bot đang chạy live.
                </p>
              </div>
              <div className="flex flex-wrap gap-2">
                <button
                  onClick={runReconcileNow}
                  disabled={reconcileNow.isPending || !workspaceId}
                  className="px-4 py-2 bg-blue-800 hover:bg-blue-700 text-white rounded-lg text-sm font-medium disabled:opacity-50"
                >
                  {reconcileNow.isPending ? 'Đang reconcile…' : 'Reconcile ngay'}
                </button>
                <button
                  onClick={runResetDailyLock}
                  disabled={!dailyLocked || resetDailyLock.isPending}
                  className="px-4 py-2 bg-amber-800 hover:bg-amber-700 text-white rounded-lg text-sm font-medium disabled:opacity-50"
                >
                  {resetDailyLock.isPending ? 'Đang mở khóa…' : 'Reset daily lock'}
                </button>
              </div>
            </div>

            {actionMessage ? (
              <div className="mt-4 rounded-lg border border-emerald-800 bg-emerald-950/60 px-3 py-2 text-sm text-emerald-300">
                {actionMessage}
              </div>
            ) : null}
            {actionError ? (
              <div className="mt-4 rounded-lg border border-red-800 bg-red-950/60 px-3 py-2 text-sm text-red-300">
                {actionError}
              </div>
            ) : null}
          </div>
        ) : null}

        {/* Runtime status */}
        <div className="bg-surface-muted rounded-xl p-4">
          <h3 className="text-lg font-semibold mb-3">Trạng thái runtime</h3>
          {runtimeLoading ? (
            <div className="animate-pulse h-16 bg-gray-800 rounded" />
          ) : runtime ? (
            <dl className="grid grid-cols-2 gap-2 text-sm">
              {Object.entries(runtime as Record<string, unknown>).map(([k, v]) => (
                <div key={k}>
                  <dt className="text-gray-500 text-xs uppercase">{toVietnameseRuntimeKey(k)}</dt>
                  <dd className="text-white">{k === 'status' ? toVietnameseStatus(String(v)) : String(v)}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p className="text-gray-500 text-sm">Bot hiện chưa chạy.</p>
          )}
        </div>

        {/* Live WS data */}
        <div className="bg-surface-muted rounded-xl p-4">
          <h3 className="text-lg font-semibold mb-3">Dữ liệu trực tiếp</h3>
          {wsData ? (
            <pre className="text-xs text-gray-300 overflow-auto max-h-40">
              {JSON.stringify(wsData, null, 2)}
            </pre>
          ) : (
            <p className="text-gray-500 text-sm">
              {isConnected ? 'Đang chờ dữ liệu…' : 'Hãy khởi động bot để nhận cập nhật trực tiếp.'}
            </p>
          )}
        </div>

        <div className="bg-surface-muted rounded-xl p-4">
          <h3 className="text-lg font-semibold mb-3">Daily safety state</h3>
          {dailyStateLoading ? (
            <div className="animate-pulse h-16 bg-gray-800 rounded" />
          ) : dailyState ? (
            <dl className="grid grid-cols-2 gap-3 text-sm">
              <div>
                <dt className="text-xs text-gray-500 uppercase tracking-wide">Trading day</dt>
                <dd className="text-white mt-0.5">{dailyState.trading_day ?? '—'}</dd>
              </div>
              <div>
                <dt className="text-xs text-gray-500 uppercase tracking-wide">Locked</dt>
                <dd className={dailyState.locked ? 'text-amber-300 mt-0.5' : 'text-emerald-300 mt-0.5'}>
                  {dailyState.locked ? 'đang_khóa' : 'đang_mở'}
                </dd>
              </div>
              <div>
                <dt className="text-xs text-gray-500 uppercase tracking-wide">Daily PnL</dt>
                <dd className="text-white mt-0.5">{Number(dailyState.daily_profit_amount ?? 0).toFixed(2)}</dd>
              </div>
              <div>
                <dt className="text-xs text-gray-500 uppercase tracking-wide">Daily loss %</dt>
                <dd className="text-white mt-0.5">{Number(dailyState.daily_loss_pct ?? 0).toFixed(2)}%</dd>
              </div>
              <div>
                <dt className="text-xs text-gray-500 uppercase tracking-wide">Consecutive losses</dt>
                <dd className="text-white mt-0.5">{dailyState.consecutive_losses ?? 0}</dd>
              </div>
              <div>
                <dt className="text-xs text-gray-500 uppercase tracking-wide">Trades count</dt>
                <dd className="text-white mt-0.5">{dailyState.trades_count ?? 0}</dd>
              </div>
              <div className="col-span-2">
                <dt className="text-xs text-gray-500 uppercase tracking-wide">Lock reason</dt>
                <dd className="text-white mt-0.5 break-words">{dailyState.lock_reason ?? '—'}</dd>
              </div>
            </dl>
          ) : (
            <p className="text-gray-500 text-sm">Chưa có daily state.</p>
          )}
        </div>

        <div className="bg-surface-muted rounded-xl p-4 md:col-span-2">
          <h3 className="text-lg font-semibold mb-3">Trading incidents</h3>
          {incidentsLoading ? (
            <div className="animate-pulse h-24 bg-gray-800 rounded" />
          ) : incidents && incidents.length > 0 ? (
            <div className="space-y-3">
              {incidents.map((incident) => {
                const isResolved = incident.status === 'resolved';
                return (
                  <div key={incident.id} className="rounded-xl border border-slate-800 bg-slate-950/40 p-4">
                    <div className="flex flex-col gap-3 lg:flex-row lg:items-start lg:justify-between">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2 mb-1">
                          <span className={`rounded-md px-2 py-0.5 text-xs font-medium ${severityBadgeClass(incident.severity)}`}>
                            {incident.severity}
                          </span>
                          <span className="rounded-md bg-slate-800 px-2 py-0.5 text-xs text-slate-300">
                            {incident.status}
                          </span>
                          <span className="text-xs text-gray-500">#{incident.id}</span>
                        </div>
                        <p className="text-sm font-medium text-white">{incident.title}</p>
                        <p className="text-sm text-gray-400 mt-1 break-words">{incident.detail || incident.incident_type}</p>
                        <p className="text-xs text-gray-500 mt-2">
                          {new Date(incident.created_at).toLocaleString()}
                        </p>
                      </div>
                      <div>
                        <button
                          onClick={() => runResolveIncident(incident.id)}
                          disabled={isResolved || resolveIncident.isPending}
                          className="px-3 py-2 bg-slate-200 text-slate-900 hover:bg-white rounded-lg text-sm font-medium disabled:opacity-50"
                        >
                          {resolveIncident.isPending ? 'Đang resolve…' : isResolved ? 'Đã resolve' : 'Resolve'}
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          ) : (
            <p className="text-gray-500 text-sm">Chưa có incident nào cho bot này.</p>
          )}
        </div>

        {/* Bot config */}
        <div className="bg-surface-muted rounded-xl p-4 md:col-span-2">
          <h3 className="text-lg font-semibold mb-3">Chi tiết</h3>
          <dl className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
            {[
              ['ID', bot.id],
              ['Cặp giao dịch', bot.symbol],
              ['Khung thời gian', bot.timeframe],
              ['Chế độ', bot.mode],
              ['Trạng thái', bot.status],
              ['Chiến lược', bot.strategy_id ?? '—'],
              ['Kết nối sàn', bot.broker_connection_id ?? '—'],
              ['Ngày tạo', new Date(bot.created_at).toLocaleDateString()],
            ].map(([label, value]) => (
              <div key={label}>
                <dt className="text-xs text-gray-500 uppercase tracking-wide">{label}</dt>
                <dd className="text-white mt-0.5 truncate">{value}</dd>
              </div>
            ))}
          </dl>
        </div>
      </div>
    </div>
  );
}


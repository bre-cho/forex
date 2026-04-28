"use client";

import { useEffect, useState } from "react";
import { getTradingBrainHealth, previewTradingBrainCycle, type BrainCycleResult } from "../../../lib/tradingBrainApi";

export default function TradingBrainPage() {
  const [health, setHealth] = useState<Record<string, unknown> | null>(null);
  const [cycle, setCycle] = useState<BrainCycleResult | null>(null);
  const [error, setError] = useState<string>("");

  async function load() {
    try {
      setError("");
      const [h, c] = await Promise.all([getTradingBrainHealth(), previewTradingBrainCycle()]);
      setHealth(h);
      setCycle(c);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Không tải được Trading Brain");
    }
  }

  useEffect(() => { void load(); }, []);

  return (
    <main className="space-y-6 p-6">
      <div className="rounded-2xl border bg-white p-6 shadow-sm">
        <h1 className="text-2xl font-bold">Bộ não giao dịch khép kín</h1>
        <p className="mt-2 text-sm text-slate-600">
          Hợp nhất AI Trading Brain và Trading Core Engines theo pipeline: dữ liệu → tín hiệu → policy → risk → execution intent → monitor → học lại.
        </p>
        <button onClick={() => void load()} className="mt-4 rounded-xl bg-slate-900 px-4 py-2 text-white">
          Chạy kiểm tra brain
        </button>
        {error && <p className="mt-3 text-sm text-red-600">{error}</p>}
      </div>

      <section className="grid gap-4 md:grid-cols-2">
        <div className="rounded-2xl border bg-white p-5 shadow-sm">
          <h2 className="font-semibold">Trạng thái engine registry</h2>
          <pre className="mt-3 max-h-96 overflow-auto rounded-xl bg-slate-50 p-3 text-xs">{JSON.stringify(health, null, 2)}</pre>
        </div>
        <div className="rounded-2xl border bg-white p-5 shadow-sm">
          <h2 className="font-semibold">Kết quả chu kỳ quyết định</h2>
          <p className="mt-2 text-sm">Action: <b>{cycle?.action ?? "—"}</b></p>
          <p className="text-sm">Lý do: <b>{cycle?.reason ?? "—"}</b></p>
          <p className="text-sm">Điểm cuối: <b>{cycle?.final_score ?? "—"}</b></p>
          <pre className="mt-3 max-h-96 overflow-auto rounded-xl bg-slate-50 p-3 text-xs">{JSON.stringify(cycle?.execution_intent ?? cycle, null, 2)}</pre>
        </div>
      </section>

      <section className="rounded-2xl border bg-white p-5 shadow-sm">
        <h2 className="font-semibold">Timeline pipeline</h2>
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead><tr><th>Stage</th><th>Action</th><th>Reason</th><th>Score</th></tr></thead>
            <tbody>
              {(cycle?.stage_decisions ?? []).map((s) => (
                <tr key={`${s.stage}-${s.reason}`} className="border-t">
                  <td className="py-2">{s.stage}</td><td>{s.action}</td><td>{s.reason}</td><td>{s.score}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </main>
  );
}

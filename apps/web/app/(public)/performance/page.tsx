interface LeaderboardItem {
  bot_id: string;
  bot_name: string;
  symbol: string;
  timeframe: string;
  mode: string;
  total_trades: number;
  total_pnl: number;
  win_rate: number;
}

interface LeaderboardData {
  items: LeaderboardItem[];
  count: number;
}

async function fetchLeaderboard(): Promise<LeaderboardData> {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  try {
    const res = await fetch(`${apiUrl}/v1/public/performance/leaderboard`, {
      next: { revalidate: 120 },
    });
    if (!res.ok) return { items: [], count: 0 };
    return res.json();
  } catch {
    return { items: [], count: 0 };
  }
}

export default async function PerformancePage() {
  const data = await fetchLeaderboard();
  const items = data.items ?? [];

  return (
    <div className="min-h-screen bg-surface py-20 px-4">
      <h1 className="text-4xl font-bold text-white text-center mb-3">Bảng xếp hạng hiệu suất</h1>
      <p className="text-gray-400 text-center text-sm mb-12">
        Các bot công khai hiệu quả nhất được xếp hạng theo tổng PnL đã chốt.
      </p>

      <div className="max-w-4xl mx-auto">
        {items.length === 0 ? (
          <div className="bg-surface-muted rounded-xl p-8 text-gray-400 text-center">
            Chưa có bot công khai nào có giao dịch đã đóng. Vui lòng quay lại sau.
          </div>
        ) : (
          <div className="bg-surface-muted rounded-xl overflow-hidden">
            <table className="w-full text-sm text-left">
              <thead className="border-b border-gray-700 text-gray-400 text-xs uppercase">
                <tr>
                  <th className="px-4 py-3">#</th>
                  <th className="px-4 py-3">Bot</th>
                  <th className="px-4 py-3">Cặp</th>
                  <th className="px-4 py-3">Chế độ</th>
                  <th className="px-4 py-3 text-right">Số lệnh</th>
                  <th className="px-4 py-3 text-right">Tỷ lệ thắng</th>
                  <th className="px-4 py-3 text-right">Tổng PnL</th>
                </tr>
              </thead>
              <tbody>
                {items.map((item, idx) => (
                  <tr
                    key={item.bot_id}
                    className="border-b border-gray-800 hover:bg-surface transition-colors"
                  >
                    <td className="px-4 py-3 text-gray-500">{idx + 1}</td>
                    <td className="px-4 py-3 text-white font-medium">{item.bot_name}</td>
                    <td className="px-4 py-3 text-gray-300">
                      {item.symbol} / {item.timeframe}
                    </td>
                    <td className="px-4 py-3">
                      <span
                        className={`px-2 py-0.5 rounded text-xs font-medium ${
                          item.mode === 'live'
                            ? 'bg-green-900 text-green-300'
                            : 'bg-gray-700 text-gray-300'
                        }`}
                      >
                        {item.mode}
                      </span>
                    </td>
                    <td className="px-4 py-3 text-right text-gray-300">{item.total_trades}</td>
                    <td className="px-4 py-3 text-right text-gray-300">
                      {(item.win_rate * 100).toFixed(1)}%
                    </td>
                    <td
                      className={`px-4 py-3 text-right font-semibold ${
                        item.total_pnl >= 0 ? 'text-green-400' : 'text-red-400'
                      }`}
                    >
                      {item.total_pnl >= 0 ? '+' : ''}
                      {item.total_pnl.toFixed(2)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}


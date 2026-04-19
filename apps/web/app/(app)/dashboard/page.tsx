'use client';
export default function DashboardPage() {
  return (
    <div>
      <h1 className="text-3xl font-bold mb-6">Dashboard</h1>
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        {[
          { label: 'Active Bots', value: '—' },
          { label: 'Total PnL', value: '—' },
          { label: 'Win Rate', value: '—' },
          { label: 'Open Trades', value: '—' },
        ].map((stat) => (
          <div key={stat.label} className="bg-surface-muted rounded-xl p-4">
            <p className="text-gray-400 text-sm">{stat.label}</p>
            <p className="text-2xl font-bold mt-1">{stat.value}</p>
          </div>
        ))}
      </div>
    </div>
  );
}

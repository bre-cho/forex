export default function AdminDashboard() {
  return (
    <div>
      <h1 style={{ fontSize: '28px', fontWeight: 'bold', marginBottom: '24px' }}>Admin Dashboard</h1>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '16px' }}>
        {['Users', 'Workspaces', 'Bots', 'Subscriptions'].map((label) => (
          <div key={label} style={{ background: '#2a2a3e', borderRadius: '12px', padding: '16px' }}>
            <p style={{ color: '#888', fontSize: '13px' }}>{label}</p>
            <p style={{ fontSize: '28px', fontWeight: 'bold', color: '#10b981' }}>—</p>
          </div>
        ))}
      </div>
    </div>
  );
}

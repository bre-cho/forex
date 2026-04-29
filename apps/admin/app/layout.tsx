export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const labels: Record<string, string> = {
    '/': 'Tổng quan',
    '/users': 'Người dùng',
    '/workspaces': 'Không gian làm việc',
    '/runtime': 'Bộ máy chạy',
    '/broker-health': 'Sức khỏe kết nối sàn',
    '/operations-dashboard': 'Dashboard vận hành',
  };

  return (
    <html lang="vi">
      <body style={{ background: '#1e1e2e', color: 'white', fontFamily: 'sans-serif' }}>
        <nav style={{ background: '#2a2a3e', padding: '12px 24px', display: 'flex', gap: '24px' }}>
          <strong style={{ color: '#10b981' }}>Quản trị</strong>
          {['/', '/users', '/workspaces', '/runtime', '/broker-health', '/operations-dashboard'].map((path) => (
            <a key={path} href={path} style={{ color: '#aaa', textDecoration: 'none', fontSize: '14px' }}>
              {labels[path] ?? path}
            </a>
          ))}
        </nav>
        <main style={{ padding: '32px' }}>{children}</main>
      </body>
    </html>
  );
}

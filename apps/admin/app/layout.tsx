export default function AdminLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ background: '#1e1e2e', color: 'white', fontFamily: 'sans-serif' }}>
        <nav style={{ background: '#2a2a3e', padding: '12px 24px', display: 'flex', gap: '24px' }}>
          <strong style={{ color: '#10b981' }}>Admin</strong>
          {['/', '/users', '/workspaces', '/runtime', '/broker-health'].map((path) => (
            <a key={path} href={path} style={{ color: '#aaa', textDecoration: 'none', fontSize: '14px' }}>
              {path === '/'
                ? 'Dashboard'
                : path
                    .replace('/', '')
                    .split('-')
                    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
                    .join(' ')}
            </a>
          ))}
        </nav>
        <main style={{ padding: '32px' }}>{children}</main>
      </body>
    </html>
  );
}

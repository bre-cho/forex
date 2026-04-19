'use client';
import Link from 'next/link';
import { usePathname } from 'next/navigation';

const navItems = [
  { href: '/dashboard', label: 'Dashboard' },
  { href: '/bots', label: 'Bots' },
  { href: '/signals', label: 'Signals' },
  { href: '/analytics', label: 'Analytics' },
  { href: '/broker-connections', label: 'Brokers' },
  { href: '/billing', label: 'Billing' },
  { href: '/notifications', label: 'Notifications' },
  { href: '/settings', label: 'Settings' },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  return (
    <div className="flex min-h-screen bg-surface">
      <aside className="w-56 bg-surface-muted flex flex-col py-6 px-4">
        <h1 className="text-brand font-bold text-xl mb-8">ForexBot</h1>
        <nav className="flex flex-col gap-1">
          {navItems.map((item) => (
            <Link
              key={item.href} href={item.href}
              className={`px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                pathname?.startsWith(item.href)
                  ? 'bg-brand text-white'
                  : 'text-gray-300 hover:bg-surface hover:text-white'
              }`}
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </aside>
      <main className="flex-1 p-8 text-white">{children}</main>
    </div>
  );
}

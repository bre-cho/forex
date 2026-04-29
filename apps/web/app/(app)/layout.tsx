'use client';
import Link from 'next/link';
import type { Route } from 'next';
import { usePathname } from 'next/navigation';

const navItems = [
  { href: '/dashboard', label: 'Tổng quan' },
  { href: '/bots', label: 'Bot' },
  { href: '/signals', label: 'Tín hiệu' },
  { href: '/live-orders', label: 'Lệnh trực tiếp' },
  { href: '/trades', label: 'Giao dịch' },
  { href: '/runtime-control', label: 'Điều khiển runtime' },
  { href: '/live-control-center', label: 'Live Control Center' },
  { href: '/analytics', label: 'Phân tích' },
  { href: '/broker-connections', label: 'Kết nối sàn' },
  { href: '/billing', label: 'Thanh toán' },
  { href: '/notifications', label: 'Thông báo' },
  { href: '/settings', label: 'Cài đặt' },
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
              key={item.href} href={item.href as Route}
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

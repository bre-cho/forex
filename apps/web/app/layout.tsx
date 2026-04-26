import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Nền Tảng Giao Dịch Forex',
  description: 'Giao dịch forex thuật toán với AI và phân tích sóng Elliott',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi">
      <body className="bg-surface text-white antialiased">{children}</body>
    </html>
  );
}

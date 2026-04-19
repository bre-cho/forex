import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: 'Forex Trading Platform',
  description: 'Algorithmic forex trading with AI and Elliott Wave analysis',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="bg-surface text-white antialiased">{children}</body>
    </html>
  );
}

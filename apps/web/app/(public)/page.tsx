import Link from 'next/link';

export default function LandingPage() {
  return (
    <main className="min-h-screen bg-surface flex flex-col items-center justify-center text-white">
      <h1 className="text-5xl font-bold mb-4 text-brand">Forex Trading Platform</h1>
      <p className="text-xl text-gray-300 mb-8 max-w-2xl text-center">
        Algorithmic trading powered by Elliott Wave analysis, multi-agent AI, and real-time broker execution.
      </p>
      <div className="flex gap-4">
        <Link href="/register" className="px-6 py-3 bg-brand text-white rounded-lg font-semibold hover:bg-brand-dark">
          Get Started
        </Link>
        <Link href="/strategies" className="px-6 py-3 border border-brand text-brand rounded-lg font-semibold hover:bg-surface-muted">
          View Strategies
        </Link>
      </div>
    </main>
  );
}

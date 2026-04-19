import { Suspense } from 'react';

interface PublicStrategy {
  id: string;
  name: string;
  description: string;
  config: Record<string, unknown>;
}

async function fetchStrategies(): Promise<PublicStrategy[]> {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  try {
    const res = await fetch(`${apiUrl}/v1/public/strategies`, {
      next: { revalidate: 60 },
    });
    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
}

async function StrategiesList() {
  const strategies = await fetchStrategies();

  if (strategies.length === 0) {
    return (
      <div className="bg-surface-muted p-8 rounded-xl text-center text-gray-400">
        No public strategies yet. Check back soon.
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
      {strategies.map((s) => (
        <a
          key={s.id}
          href={`/strategies/${s.id}`}
          className="bg-surface-muted p-6 rounded-xl text-white hover:ring-2 hover:ring-brand transition-all block"
        >
          <h3 className="text-lg font-bold text-brand">{s.name}</h3>
          <p className="text-gray-400 mt-2 text-sm line-clamp-3">
            {s.description || 'No description available.'}
          </p>
          <p className="text-xs text-gray-600 mt-4">ID: {s.id}</p>
        </a>
      ))}
    </div>
  );
}

export default function StrategiesPage() {
  return (
    <div className="min-h-screen bg-surface py-20 px-4">
      <h1 className="text-4xl font-bold text-white text-center mb-4">Public Strategies</h1>
      <p className="text-gray-400 text-center mb-12 text-sm">
        Strategies shared by our community. Click to view details.
      </p>
      <div className="max-w-5xl mx-auto">
        <Suspense
          fallback={
            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
              {Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="bg-surface-muted p-6 rounded-xl animate-pulse h-32" />
              ))}
            </div>
          }
        >
          <StrategiesList />
        </Suspense>
      </div>
    </div>
  );
}


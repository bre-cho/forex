import { notFound } from 'next/navigation';

interface Strategy {
  id: string;
  name: string;
  description: string;
  is_public: boolean;
  config: Record<string, unknown>;
  created_at: string;
}

async function fetchStrategy(slug: string): Promise<Strategy | null> {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  // The public strategies list uses ID as the slug (no vanity slug yet).
  try {
    const listRes = await fetch(`${apiUrl}/v1/public/strategies`, {
      next: { revalidate: 60 },
    });
    if (!listRes.ok) return null;
    const strategies: Strategy[] = await listRes.json();
    return strategies.find((s) => s.id === slug) ?? null;
  } catch {
    return null;
  }
}

export default async function StrategyDetailPage({
  params,
}: {
  params: { slug: string };
}) {
  const strategy = await fetchStrategy(params.slug);
  if (!strategy) notFound();

  const configEntries = Object.entries(strategy.config);

  return (
    <div className="min-h-screen bg-surface py-20 px-4">
      <div className="max-w-3xl mx-auto">
        {/* Back link */}
        <a href="/strategies" className="text-brand text-sm hover:underline mb-6 inline-block">
          ← Back to Strategies
        </a>

        {/* Header */}
        <h1 className="text-4xl font-bold text-white mb-3">{strategy.name}</h1>
        <p className="text-gray-400 mb-8">
          {strategy.description || 'No description provided.'}
        </p>

        {/* Config card */}
        {configEntries.length > 0 && (
          <div className="bg-surface-muted rounded-xl p-6 mb-6">
            <h2 className="text-lg font-semibold text-white mb-4">Configuration</h2>
            <dl className="grid grid-cols-2 gap-4">
              {configEntries.map(([key, val]) => (
                <div key={key}>
                  <dt className="text-xs text-gray-500 uppercase tracking-wide">{key}</dt>
                  <dd className="text-white font-medium mt-0.5">
                    {String(val)}
                  </dd>
                </div>
              ))}
            </dl>
          </div>
        )}

        {/* Metadata */}
        <div className="text-xs text-gray-600">
          <span>Strategy ID: {strategy.id}</span>
          {strategy.created_at && (
            <span className="ml-4">
              Published: {new Date(strategy.created_at).toLocaleDateString()}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}

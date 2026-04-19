import { Suspense } from 'react';

async function StrategiesList() {
  return (
    <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
      <div className="bg-surface-muted p-6 rounded-xl text-white">
        <h3 className="text-lg font-bold text-brand">Wave + AI Demo Strategy</h3>
        <p className="text-gray-400 mt-2 text-sm">Elliott Wave analysis with LLM confirmation</p>
      </div>
    </div>
  );
}

export default function StrategiesPage() {
  return (
    <div className="min-h-screen bg-surface py-20 px-4">
      <h1 className="text-4xl font-bold text-white text-center mb-12">Public Strategies</h1>
      <div className="max-w-5xl mx-auto">
        <Suspense fallback={<p className="text-gray-400">Loading...</p>}>
          <StrategiesList />
        </Suspense>
      </div>
    </div>
  );
}

'use client';
import Link from 'next/link';

export default function BotsPage() {
  return (
    <div>
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-3xl font-bold">Bots</h1>
        <button className="px-4 py-2 bg-brand text-white rounded-lg">+ New Bot</button>
      </div>
      <p className="text-gray-400">Select a workspace to view bots.</p>
    </div>
  );
}

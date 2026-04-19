'use client';
import { use } from 'react';
import { useBotRuntime } from '@/hooks/useBots';
import { useBotWebSocket } from '@/hooks/useWebSocket';

export default function BotDetailPage({ params }: { params: { id: string } }) {
  const botId = params.id;
  const { isConnected } = useBotWebSocket(botId);

  return (
    <div>
      <h1 className="text-3xl font-bold mb-6">Bot: {botId}</h1>
      <div className="flex items-center gap-2 mb-4">
        <div className={`w-2 h-2 rounded-full ${isConnected ? 'bg-green-400' : 'bg-gray-400'}`} />
        <span className="text-sm text-gray-400">{isConnected ? 'Live' : 'Disconnected'}</span>
      </div>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <div className="bg-surface-muted rounded-xl p-4">
          <h3 className="text-lg font-semibold mb-2">Runtime Status</h3>
          <p className="text-gray-400 text-sm">Connect to see live updates</p>
        </div>
      </div>
    </div>
  );
}

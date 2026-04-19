'use client';
import { useEffect, useRef, useState } from 'react';

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

export function useWebSocket<T = unknown>(path: string) {
  const [data, setData] = useState<T | null>(null);
  const [isConnected, setIsConnected] = useState(false);
  const ws = useRef<WebSocket | null>(null);

  useEffect(() => {
    const wsUrl = API_URL.replace(/^http/, 'ws') + path;
    ws.current = new WebSocket(wsUrl);

    ws.current.onopen = () => setIsConnected(true);
    ws.current.onclose = () => setIsConnected(false);
    ws.current.onmessage = (event) => {
      try {
        setData(JSON.parse(event.data));
      } catch {
        // ignore non-JSON messages (e.g. "pong")
      }
    };

    const keepAlive = setInterval(() => {
      if (ws.current?.readyState === WebSocket.OPEN) {
        ws.current.send('ping');
      }
    }, 30_000);

    return () => {
      clearInterval(keepAlive);
      ws.current?.close();
    };
  }, [path]);

  return { data, isConnected };
}

export function useBotWebSocket(botId: string) {
  return useWebSocket(`/ws/bots/${botId}`);
}

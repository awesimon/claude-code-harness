import { useEffect, useRef, useCallback, useState } from 'react';
import { useChatStore } from '@/stores/chatStore';
import { checkHealth } from '@/lib/api';

export function useSSE() {
  const store = useChatStore();
  const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [reconnectAttempts, setReconnectAttempts] = useState(0);
  const maxReconnectAttempts = 5;

  const checkConnection = useCallback(async () => {
    const isHealthy = await checkHealth();
    if (isHealthy) {
      store.setConnectionStatus('connected');
      setReconnectAttempts(0);
    } else {
      store.setConnectionStatus('error');
    }
    return isHealthy;
  }, [store]);

  const reconnect = useCallback(() => {
    if (reconnectAttempts >= maxReconnectAttempts) {
      store.setConnectionStatus('error');
      return;
    }

    store.setConnectionStatus('connecting');
    const delay = Math.min(1000 * Math.pow(2, reconnectAttempts), 30000);

    reconnectTimeoutRef.current = setTimeout(() => {
      setReconnectAttempts((prev) => prev + 1);
      checkConnection();
    }, delay);
  }, [reconnectAttempts, checkConnection, store]);

  useEffect(() => {
    // Initial connection check
    checkConnection();

    // Periodic health check
    const interval = setInterval(() => {
      checkConnection();
    }, 30000);

    return () => {
      clearInterval(interval);
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
    };
  }, [checkConnection]);

  return {
    connectionStatus: store.connectionStatus,
    reconnect,
    reconnectAttempts,
  };
}

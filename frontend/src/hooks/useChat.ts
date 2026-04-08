import { useCallback, useRef, useState } from 'react';
import { useChatStore } from '@/stores/chatStore';
import { streamMessage, createConversation, deleteConversation } from '@/lib/api';
import type { ToolCall, ToolResult } from '@/types';

export function useChat() {
  const store = useChatStore();
  const abortControllerRef = useRef<AbortController | null>(null);
  const [retryCount, setRetryCount] = useState(0);

  const sendMessage = useCallback(
    async (content: string) => {
      if (store.isProcessing || !content.trim()) return;

      let conversationId = store.currentConversationId;

      // Create new conversation if needed
      if (!conversationId) {
        try {
          conversationId = await createConversation();
          store.addConversation({
            id: conversationId,
            title: content.slice(0, 30) + (content.length > 30 ? '...' : ''),
            messages: [],
            createdAt: Date.now(),
            updatedAt: Date.now(),
          });
        } catch (error) {
          store.setError('Failed to create conversation');
          return;
        }
      }

      // Add user message
      store.addMessage({ role: 'user', content });
      store.setProcessing(true);
      store.setStatus('thinking');
      store.setError(null);

      // Create abort controller for cancellation
      abortControllerRef.current = new AbortController();

      let assistantMessageId: string | null = null;
      let currentToolCalls: ToolCall[] = [];

      try {
        await streamMessage(
          {
            message: content,
            conversation_id: conversationId,
          },
          {
            onEvent: (event) => {
              switch (event.type) {
                case 'assistant_message':
                  if (!assistantMessageId) {
                    assistantMessageId = store.addMessage({
                      role: 'assistant',
                      content: event.content || '',
                    });
                    store.setStatus('streaming');
                  } else {
                    store.updateMessage(assistantMessageId, event.content || '');
                  }
                  break;

                case 'tool_call':
                  store.setStatus('tool_calling');
                  if (event.tool_calls) {
                    currentToolCalls = [...currentToolCalls, ...event.tool_calls];
                  }
                  break;

                case 'tool_result':
                  // Tool results are handled separately
                  break;

                case 'state_change':
                  if (event.state === 'thinking') {
                    store.setStatus('thinking');
                  } else if (event.state === 'tool_calling') {
                    store.setStatus('tool_calling');
                  } else {
                    store.setStatus('streaming');
                  }
                  break;

                case 'error':
                  store.setError(event.error || 'Unknown error');
                  break;
              }
            },
            onError: (error) => {
              store.setError(error.message);
              store.setProcessing(false);
              store.setStatus('idle');

              // Auto retry on network errors
              if (retryCount < 3 && error.message.includes('network')) {
                setRetryCount((c) => c + 1);
                setTimeout(() => sendMessage(content), 1000 * (retryCount + 1));
              }
            },
            onComplete: () => {
              store.setProcessing(false);
              store.setStatus('idle');
              setRetryCount(0);
            },
          },
          abortControllerRef.current.signal
        );
      } catch (error) {
        store.setError(error instanceof Error ? error.message : 'Unknown error');
        store.setProcessing(false);
        store.setStatus('idle');
      }
    },
    [store, retryCount]
  );

  const cancelMessage = useCallback(() => {
    if (abortControllerRef.current) {
      abortControllerRef.current.abort();
      abortControllerRef.current = null;
    }
    store.setProcessing(false);
    store.setStatus('idle');
  }, [store]);

  const clearChat = useCallback(async () => {
    if (store.currentConversationId) {
      try {
        await deleteConversation(store.currentConversationId);
      } catch {
        // Ignore delete errors
      }
    }
    store.clearMessages();
    store.setCurrentConversation(null);
  }, [store]);

  const newChat = useCallback(async () => {
    try {
      const conversationId = await createConversation();
      store.addConversation({
        id: conversationId,
        title: 'New Chat',
        messages: [],
        createdAt: Date.now(),
        updatedAt: Date.now(),
      });
      return conversationId;
    } catch (error) {
      store.setError('Failed to create conversation');
      return null;
    }
  }, [store]);

  return {
    messages: store.messages,
    isProcessing: store.isProcessing,
    status: store.status,
    error: store.error,
    currentConversationId: store.currentConversationId,
    sendMessage,
    cancelMessage,
    clearChat,
    newChat,
  };
}

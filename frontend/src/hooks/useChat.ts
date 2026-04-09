import { useCallback, useRef, useState } from 'react';
import { useChatStore } from '@/stores/chatStore';
import { streamMessage, createConversation, deleteConversation } from '@/lib/api';
import type { ToolCall, ToolResult } from '@/types';

export function useChat() {
  const store = useChatStore();
  const abortControllerRef = useRef<AbortController | null>(null);
  const [retryCount, setRetryCount] = useState(0);

  // Track the current streaming state
  const streamingStateRef = useRef<{
    currentAssistantId: string | null;
    hasReceivedToolCalls: boolean;
  }>({ currentAssistantId: null, hasReceivedToolCalls: false });

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

      // Reset streaming state
      streamingStateRef.current = { currentAssistantId: null, hasReceivedToolCalls: false };

      // Create abort controller for cancellation
      abortControllerRef.current = new AbortController();

      try {
        await streamMessage(
          {
            message: content,
            conversation_id: conversationId,
          },
          {
            onEvent: (event) => {
              switch (event.type) {
                case 'assistant_message': {
                  const state = streamingStateRef.current;

                  // If we've already received tool calls, this is a NEW assistant message
                  // after tool execution - create a new message
                  if (state.hasReceivedToolCalls) {
                    const newMessageId = store.addMessage({
                      role: 'assistant',
                      content: event.content || '',
                    });
                    state.currentAssistantId = newMessageId;
                    state.hasReceivedToolCalls = false;
                  } else if (!state.currentAssistantId) {
                    // First assistant message - create new
                    const newMessageId = store.addMessage({
                      role: 'assistant',
                      content: event.content || '',
                    });
                    state.currentAssistantId = newMessageId;
                  } else {
                    // Continue appending to current message
                    if (event.content) {
                      store.updateMessage(state.currentAssistantId, event.content);
                    }
                  }

                  if (event.is_streaming) {
                    store.setStatus('streaming');
                  }
                  break;
                }

                case 'tool_call':
                  store.setStatus('tool_calling');
                  streamingStateRef.current.hasReceivedToolCalls = true;

                  if (event.tool_calls && streamingStateRef.current.currentAssistantId) {
                    store.updateMessageToolCalls(
                      streamingStateRef.current.currentAssistantId,
                      event.tool_calls
                    );
                  }
                  break;

                case 'tool_result':
                  if (streamingStateRef.current.currentAssistantId) {
                    const toolResult: ToolResult = {
                      id: event.tool_call_id || '',
                      name: event.name || '',
                      success: event.success || false,
                      result: event.result,
                      error: event.error,
                    };
                    store.updateMessageToolResults(
                      streamingStateRef.current.currentAssistantId,
                      [toolResult]
                    );
                  }
                  break;

                case 'thinking':
                  // 处理思考过程
                  if (streamingStateRef.current.currentAssistantId && event.content) {
                    store.updateMessageThinking(
                      streamingStateRef.current.currentAssistantId,
                      event.content
                    );
                  }
                  break;

                case 'state_change':
                  if (event.state === 'thinking') {
                    store.setStatus('thinking');
                  } else if (event.state === 'tool_calling') {
                    store.setStatus('tool_calling');
                  } else if (event.state === 'observing') {
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
              streamingStateRef.current = { currentAssistantId: null, hasReceivedToolCalls: false };
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
    streamingStateRef.current = { currentAssistantId: null, hasReceivedToolCalls: false };
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
    streamingStateRef.current = { currentAssistantId: null, hasReceivedToolCalls: false };
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
      streamingStateRef.current = { currentAssistantId: null, hasReceivedToolCalls: false };
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

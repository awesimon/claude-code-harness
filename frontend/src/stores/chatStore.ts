import { create } from 'zustand';
import type { ChatState, ChatActions, Message, Conversation, ModelType } from '@/types';
import * as api from '@/lib/api';

const generateId = () => Math.random().toString(36).substring(2, 15);

const createMessage = (role: Message['role'], content: string, toolCalls?: Message['toolCalls'], toolResults?: Message['toolResults']): Message => ({
  id: generateId(),
  role,
  content,
  timestamp: Date.now(),
  toolCalls,
  toolResults,
});

interface ExtendedChatState extends ChatState {
  // Additional state for API integration
  isLoadingConversations: boolean;
  isLoadingMessages: boolean;
  wsConnection: WebSocket | null;
}

interface ExtendedChatActions extends ChatActions {
  // Additional actions for API integration
  loadConversations: () => Promise<void>;
  loadConversation: (id: string) => Promise<void>;
  createConversation: (title?: string) => Promise<string>;
  deleteConversation: (id: string) => Promise<void>;
  saveMessage: (conversationId: string, message: Message) => Promise<void>;
  connectWebSocket: (conversationId?: string) => void;
  disconnectWebSocket: () => void;
}

export const useChatStore = create<ExtendedChatState & ExtendedChatActions>()(
  (set, get) => ({
    // State
    conversations: [],
    currentConversationId: null,
    messages: [],
    isProcessing: false,
    status: 'idle',
    connectionStatus: 'disconnected',
    selectedModel: 'gpt-4o',
    error: null,
    isLoadingConversations: false,
    isLoadingMessages: false,
    wsConnection: null,

    // Actions
    setCurrentConversation: (id) => {
      set({ currentConversationId: id });
      if (id) {
        get().loadConversation(id);
        get().connectWebSocket(id);
      } else {
        set({ messages: [] });
        get().disconnectWebSocket();
      }
    },

    loadConversations: async () => {
      set({ isLoadingConversations: true });
      try {
        const conversations = await api.listConversations();
        set({ conversations });
      } catch (error) {
        console.error('Failed to load conversations:', error);
        set({ error: 'Failed to load conversations' });
      } finally {
        set({ isLoadingConversations: false });
      }
    },

    loadConversation: async (id) => {
      set({ isLoadingMessages: true });
      try {
        const conversation = await api.getConversation(id);
        set({
          messages: conversation.messages.map((m: any) => ({
            id: m.id,
            role: m.role as Message['role'],
            content: m.content,
            timestamp: new Date(m.timestamp).getTime(),
            toolCalls: m.tool_calls,
            toolResults: m.tool_results,
            thinking: m.thinking,
          })),
        });
      } catch (error) {
        console.error('Failed to load conversation:', error);
        set({ error: 'Failed to load conversation' });
      } finally {
        set({ isLoadingMessages: false });
      }
    },

    createConversation: async (title) => {
      try {
        const conversation = await api.createConversation(title);
        set((state) => ({
          conversations: [conversation, ...state.conversations],
          currentConversationId: conversation.id,
          messages: [],
        }));
        get().connectWebSocket(conversation.id);
        return conversation.id;
      } catch (error) {
        console.error('Failed to create conversation:', error);
        set({ error: 'Failed to create conversation' });
        throw error;
      }
    },

    deleteConversation: async (id) => {
      try {
        await api.deleteConversation(id);
        set((state) => {
          const newConversations = state.conversations.filter((c) => c.id !== id);
          if (state.currentConversationId === id) {
            return {
              conversations: newConversations,
              currentConversationId: null,
              messages: [],
            };
          }
          return { conversations: newConversations };
        });
      } catch (error) {
        console.error('Failed to delete conversation:', error);
        set({ error: 'Failed to delete conversation' });
      }
    },

    saveMessage: async (conversationId, message) => {
      try {
        await api.addMessage(
          conversationId,
          message.role,
          message.content,
          message.toolCalls,
          message.toolResults
        );
      } catch (error) {
        console.error('Failed to save message:', error);
      }
    },

    addMessage: (message) => {
      const newMessage = createMessage(message.role, message.content, message.toolCalls, message.toolResults);
      set((state) => {
        let newMessages = [...state.messages, newMessage];
        // Limit message list length
        const maxMessages = 100;
        if (newMessages.length > maxMessages) {
          newMessages = newMessages.slice(-maxMessages);
        }

        // Save to API if in a conversation
        if (state.currentConversationId) {
          get().saveMessage(state.currentConversationId, newMessage);
        }

        return { messages: newMessages };
      });
      return newMessage.id;
    },

    updateMessage: (id, content) => {
      set((state) => {
        const newMessages = state.messages.map((m) => {
          if (m.id === id) {
            const newContent = m.content + content;
            const maxLength = 100000;
            const truncatedContent = newContent.length > maxLength
              ? newContent.slice(0, maxLength) + '\n\n[消息内容已截断...]'
              : newContent;
            return { ...m, content: truncatedContent };
          }
          return m;
        });
        return { messages: newMessages };
      });
    },

    updateMessageToolCalls: (id, toolCalls) => {
      set((state) => {
        const newMessages = state.messages.map((m) => {
          if (m.id === id) {
            const existing = m.toolCalls || [];
            const combined = [...existing, ...toolCalls];
            const limited = combined.slice(-20);
            return { ...m, toolCalls: limited };
          }
          return m;
        });
        return { messages: newMessages };
      });
    },

    updateMessageToolResults: (id, toolResults) => {
      set((state) => {
        const newMessages = state.messages.map((m) => {
          if (m.id === id) {
            const existing = m.toolResults || [];
            const combined = [...existing, ...toolResults];
            const limited = combined.slice(-20);
            return { ...m, toolResults: limited };
          }
          return m;
        });
        return { messages: newMessages };
      });
    },

    updateMessageThinking: (id, thinking) => {
      set((state) => {
        const newMessages = state.messages.map((m) => {
          if (m.id === id) {
            const newThinking = (m.thinking || '') + thinking;
            const maxThinkingLength = 50000;
            const truncatedThinking = newThinking.length > maxThinkingLength
              ? newThinking.slice(0, maxThinkingLength) + '\n\n[思考内容已截断...]'
              : newThinking;
            return { ...m, thinking: truncatedThinking };
          }
          return m;
        });
        return { messages: newMessages };
      });
    },

    setProcessing: (isProcessing) => set({ isProcessing }),

    setStatus: (status) => set({ status }),

    setConnectionStatus: (connectionStatus) => set({ connectionStatus }),

    setSelectedModel: (selectedModel) => set({ selectedModel }),

    setError: (error) => set({ error }),

    clearMessages: () => {
      set({ messages: [] });
    },

    addConversation: (conversation) => {
      set((state) => ({
        conversations: [conversation, ...state.conversations],
        currentConversationId: conversation.id,
        messages: conversation.messages,
      }));
    },

    removeConversation: (id) => {
      set((state) => {
        const newConversations = state.conversations.filter((c) => c.id !== id);
        if (state.currentConversationId === id) {
          return {
            conversations: newConversations,
            currentConversationId: null,
            messages: [],
          };
        }
        return { conversations: newConversations };
      });
    },

    connectWebSocket: (conversationId) => {
      // Disconnect existing connection
      get().disconnectWebSocket();

      try {
        const ws = api.createWebSocket(conversationId);

        ws.onopen = () => {
          set({ connectionStatus: 'connected' });
          console.log('WebSocket connected');
        };

        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            console.log('WebSocket message:', data);

            // Handle different event types
            switch (data.type) {
              case 'task_created':
              case 'task_updated':
              case 'task_deleted':
              case 'task_claimed':
                // Refresh tasks if needed
                if (get().currentConversationId) {
                  get().loadConversation(get().currentConversationId!);
                }
                break;
              case 'message_created':
                // New message from another source
                break;
              case 'conversation_updated':
                get().loadConversations();
                break;
            }
          } catch (error) {
            console.error('Failed to parse WebSocket message:', error);
          }
        };

        ws.onclose = () => {
          set({ connectionStatus: 'disconnected', wsConnection: null });
          console.log('WebSocket disconnected');
        };

        ws.onerror = (error) => {
          console.error('WebSocket error:', error);
          set({ connectionStatus: 'error' });
        };

        set({ wsConnection: ws });
      } catch (error) {
        console.error('Failed to connect WebSocket:', error);
        set({ connectionStatus: 'error' });
      }
    },

    disconnectWebSocket: () => {
      const { wsConnection } = get();
      if (wsConnection) {
        wsConnection.close();
        set({ wsConnection: null, connectionStatus: 'disconnected' });
      }
    },
  })
);

// Initialize: load conversations on store creation
if (typeof window !== 'undefined') {
  useChatStore.getState().loadConversations();
}

import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { ChatState, ChatActions, Message, Conversation, ModelType } from '@/types';

const generateId = () => Math.random().toString(36).substring(2, 15);

const createMessage = (role: Message['role'], content: string, toolCalls?: Message['toolCalls'], toolResults?: Message['toolResults']): Message => ({
  id: generateId(),
  role,
  content,
  timestamp: Date.now(),
  toolCalls,
  toolResults,
});

export const useChatStore = create<ChatState & ChatActions>()(
  persist(
    (set, get) => ({
      // State
      conversations: [],
      currentConversationId: null,
      messages: [],
      isProcessing: false,
      status: 'idle',
      connectionStatus: 'disconnected',
      selectedModel: 'claude-3-sonnet',
      error: null,

      // Actions
      setCurrentConversation: (id) => {
        set({ currentConversationId: id });
        if (id) {
          const conversation = get().conversations.find((c) => c.id === id);
          if (conversation) {
            set({ messages: conversation.messages });
          }
        }
      },

      addMessage: (message) => {
        const newMessage = createMessage(message.role, message.content, message.toolCalls, message.toolResults);
        set((state) => {
          const newMessages = [...state.messages, newMessage];
          // Update conversation messages if in a conversation
          if (state.currentConversationId) {
            const updatedConversations = state.conversations.map((c) =>
              c.id === state.currentConversationId
                ? { ...c, messages: newMessages, updatedAt: Date.now() }
                : c
            );
            return { messages: newMessages, conversations: updatedConversations };
          }
          return { messages: newMessages };
        });
        return newMessage.id;
      },

      updateMessage: (id, content) => {
        set((state) => {
          const newMessages = state.messages.map((m) =>
            m.id === id ? { ...m, content: m.content + content } : m
          );
          if (state.currentConversationId) {
            const updatedConversations = state.conversations.map((c) =>
              c.id === state.currentConversationId
                ? { ...c, messages: newMessages, updatedAt: Date.now() }
                : c
            );
            return { messages: newMessages, conversations: updatedConversations };
          }
          return { messages: newMessages };
        });
      },

      setProcessing: (isProcessing) => set({ isProcessing }),

      setStatus: (status) => set({ status }),

      setConnectionStatus: (connectionStatus) => set({ connectionStatus }),

      setSelectedModel: (selectedModel) => set({ selectedModel }),

      setError: (error) => set({ error }),

      clearMessages: () => {
        set((state) => {
          if (state.currentConversationId) {
            const updatedConversations = state.conversations.map((c) =>
              c.id === state.currentConversationId
                ? { ...c, messages: [], updatedAt: Date.now() }
                : c
            );
            return { messages: [], conversations: updatedConversations };
          }
          return { messages: [] };
        });
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

      loadConversation: (id) => {
        const conversation = get().conversations.find((c) => c.id === id);
        if (conversation) {
          set({
            currentConversationId: id,
            messages: conversation.messages,
          });
        }
      },
    }),
    {
      name: 'chat-storage',
      partialize: (state) => ({
        conversations: state.conversations,
        selectedModel: state.selectedModel,
      }),
    }
  )
);

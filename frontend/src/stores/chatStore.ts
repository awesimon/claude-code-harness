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
      selectedModel: 'gpt-4o',
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
          let newMessages = [...state.messages, newMessage];
          // 限制消息列表长度，保留最近100条消息
          const maxMessages = 100;
          if (newMessages.length > maxMessages) {
            newMessages = newMessages.slice(-maxMessages);
          }
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
          const newMessages = state.messages.map((m) => {
            if (m.id === id) {
              // 限制消息最大长度，防止性能问题
              const newContent = m.content + content;
              const maxLength = 100000;
              const truncatedContent = newContent.length > maxLength
                ? newContent.slice(0, maxLength) + '\n\n[消息内容已截断...]'
                : newContent;
              return { ...m, content: truncatedContent };
            }
            return m;
          });
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

      updateMessageToolCalls: (id, toolCalls) => {
        set((state) => {
          const newMessages = state.messages.map((m) => {
            if (m.id === id) {
              // 限制 toolCalls 数量
              const existing = m.toolCalls || [];
              const combined = [...existing, ...toolCalls];
              const limited = combined.slice(-20); // 最多保留20个
              return { ...m, toolCalls: limited };
            }
            return m;
          });
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

      updateMessageToolResults: (id, toolResults) => {
        set((state) => {
          const newMessages = state.messages.map((m) => {
            if (m.id === id) {
              // 限制 toolResults 数量
              const existing = m.toolResults || [];
              const combined = [...existing, ...toolResults];
              const limited = combined.slice(-20); // 最多保留20个
              return { ...m, toolResults: limited };
            }
            return m;
          });
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

      updateMessageThinking: (id, thinking) => {
        set((state) => {
          const newMessages = state.messages.map((m) => {
            if (m.id === id) {
              // 限制 thinking 长度
              const newThinking = (m.thinking || '') + thinking;
              const maxThinkingLength = 50000;
              const truncatedThinking = newThinking.length > maxThinkingLength
                ? newThinking.slice(0, maxThinkingLength) + '\n\n[思考内容已截断...]'
                : newThinking;
              return { ...m, thinking: truncatedThinking };
            }
            return m;
          });
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
        // 只保存最近的10个对话，限制localStorage大小
        conversations: state.conversations.slice(0, 10),
        selectedModel: state.selectedModel,
      }),
    }
  )
);

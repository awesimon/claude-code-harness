import * as React from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { Trash } from '@phosphor-icons/react';
import { Sidebar } from '@/components/chat/Sidebar';
import { MessageList } from '@/components/chat/MessageList';
import { ChatInput } from '@/components/chat/ChatInput';
import { StatusIndicator } from '@/components/chat/StatusIndicator';
import { QuickActions } from '@/components/chat/QuickActions';
import { ErrorAlert } from '@/components/ui/Alert';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Button } from '@/components/ui/Button';
import { useChat } from '@/hooks/useChat';
import { useSSE } from '@/hooks/useSSE';
import { useChatStore } from '@/stores/chatStore';
import { MagneticButton } from '@/components/ui/MagneticButton';
import type { ModelType } from '@/types';
import { cn } from '@/lib/utils';

export default function App() {
  const {
    messages,
    isProcessing,
    status,
    error,
    currentConversationId,
    sendMessage,
    cancelMessage,
    clearChat,
    newChat,
  } = useChat();

  const { connectionStatus } = useSSE();
  const { selectedModel, setSelectedModel, setError } = useChatStore();
  const [isSidebarCollapsed, setIsSidebarCollapsed] = React.useState(false);
  const messagesEndRef = React.useRef<HTMLDivElement>(null);
  const shouldReduceMotion = useReducedMotion();

  const handleQuickAction = React.useCallback(
    (command: string) => {
      sendMessage(command);
    },
    [sendMessage]
  );

  const handleModelChange = React.useCallback(
    (value: string) => {
      setSelectedModel(value as ModelType);
    },
    [setSelectedModel]
  );

  // Derive conversation title
  const conversationTitle = React.useMemo(() => {
    if (!currentConversationId) return 'New Chat';
    const store = useChatStore.getState();
    const conversation = store.conversations.find(c => c.id === currentConversationId);
    return conversation?.title || `Chat ${currentConversationId.slice(0, 8)}`;
  }, [currentConversationId]);

  return (
    <div className="flex min-h-[100dvh] bg-background text-foreground antialiased">
      {/* Sidebar */}
      <Sidebar
        onNewChat={newChat}
        isCollapsed={isSidebarCollapsed}
        onToggleCollapse={() => setIsSidebarCollapsed(!isSidebarCollapsed)}
      />

      {/* Main Content */}
      <main className="flex flex-1 flex-col min-w-0 overflow-hidden">
        {/* Header - Glass effect */}
        <header className="flex h-14 items-center justify-between glass border-b border-white/5 px-4 lg:px-6 z-10">
          <div className="flex items-center gap-4">
            <motion.h1
              key={conversationTitle}
              initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: -10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.2 }}
              className="font-medium text-foreground tracking-tight"
            >
              {conversationTitle}
            </motion.h1>
            <StatusIndicator status={status} />
          </div>

          <div className="flex items-center gap-3">
            {/* Model Select */}
            <Select value={selectedModel} onValueChange={handleModelChange}>
              <SelectTrigger className="w-[160px] glass-strong h-9">
                <SelectValue placeholder="Select model" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="gpt-4o">GPT-4o</SelectItem>
                <SelectItem value="claude-3-sonnet">Claude 3 Sonnet</SelectItem>
                <SelectItem value="claude-3-opus">Claude 3 Opus</SelectItem>
              </SelectContent>
            </Select>

            {/* Clear Chat */}
            <MagneticButton disabled={!currentConversationId || messages.length === 0}>
              <Button
                variant="ghost"
                size="icon"
                onClick={clearChat}
                disabled={!currentConversationId || messages.length === 0}
                className="hidden sm:flex"
                aria-label="Clear chat"
              >
                <Trash className="h-4 w-4" />
              </Button>
            </MagneticButton>
          </div>
        </header>

        {/* Error Alert */}
        <AnimatePresence>
          {error && (
            <motion.div
              initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: 'auto' }}
              exit={{ opacity: 0, height: 0 }}
              className="px-4 pt-4"
            >
              <ErrorAlert error={error} onDismiss={() => setError(null)} />
            </motion.div>
          )}
        </AnimatePresence>

        {/* Messages */}
        <div className="flex-1 overflow-hidden">
          <MessageList
            ref={messagesEndRef}
            messages={messages}
            isLoading={isProcessing && status !== 'thinking'}
          />
        </div>

        {/* Quick Actions - shown when no messages */}
        <AnimatePresence>
          {messages.length === 0 && !isProcessing && (
            <motion.div
              initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 20 }}
              transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
              className="mx-auto w-full max-w-2xl px-4 pb-6"
            >
              <QuickActions onSelect={handleQuickAction} />
            </motion.div>
          )}
        </AnimatePresence>

        {/* Input */}
        <ChatInput
          onSend={sendMessage}
          onStop={cancelMessage}
          disabled={connectionStatus !== 'connected'}
          isLoading={isProcessing}
        />
      </main>
    </div>
  );
}

import * as React from 'react';
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
import { ThemeToggleSimple } from '@/components/ThemeToggle';
import { fetchModels, selectModel, type ModelInfo } from '@/services/modelService';
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
  const [models, setModels] = React.useState<ModelInfo[]>([]);
  const [isLoadingModels, setIsLoadingModels] = React.useState(true);
  const messagesEndRef = React.useRef<HTMLDivElement>(null);

  // 加载模型列表
  React.useEffect(() => {
    const loadModels = async () => {
      try {
        setIsLoadingModels(true);
        const data = await fetchModels();
        setModels(data.models);
        // 如果没有选中的模型，使用默认模型
        if (!selectedModel && data.default_model) {
          setSelectedModel(data.default_model);
        }
      } catch (err) {
        console.error('Failed to load models:', err);
        setError('Failed to load models');
      } finally {
        setIsLoadingModels(false);
      }
    };

    loadModels();
  }, [selectedModel, setSelectedModel, setError]);

  const handleQuickAction = React.useCallback(
    (command: string) => {
      sendMessage(command);
    },
    [sendMessage]
  );

  const handleModelChange = React.useCallback(
    async (value: string) => {
      setSelectedModel(value);
      try {
        await selectModel(value);
      } catch (err) {
        console.error('Failed to select model:', err);
      }
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
    <div className="flex min-h-[100dvh] bg-background text-foreground">
      {/* Sidebar */}
      <Sidebar
        onNewChat={newChat}
        isCollapsed={isSidebarCollapsed}
        onToggleCollapse={() => setIsSidebarCollapsed(!isSidebarCollapsed)}
      />

      {/* Main Content */}
      <main className="flex flex-1 flex-col min-w-0 overflow-hidden">
        {/* Header - Clean flat design */}
        <header className="flex h-14 items-center justify-between border-b border-border px-4 lg:px-6 bg-background">
          <div className="flex items-center gap-3">
            <h1 className="font-medium text-foreground">
              {conversationTitle}
            </h1>
            <StatusIndicator status={status} />
          </div>

          <div className="flex items-center gap-2">
            {/* Theme Toggle */}
            <ThemeToggleSimple />

            {/* Model Select */}
            <Select value={selectedModel} onValueChange={handleModelChange} disabled={isLoadingModels}>
              <SelectTrigger className="w-[160px] h-9 border-border bg-background">
                <SelectValue placeholder={isLoadingModels ? "Loading..." : "Select model"} />
              </SelectTrigger>
              <SelectContent>
                {models.map((model) => (
                  <SelectItem key={model.model_id} value={model.model_id}>
                    <div className="flex flex-col">
                      <span>{model.name}</span>
                      <span className="text-xs text-muted-foreground">{model.provider}</span>
                    </div>
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            {/* Clear Chat */}
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
          </div>
        </header>

        {/* Error Alert */}
        {error && (
          <div className="px-4 pt-4">
            <ErrorAlert error={error} onDismiss={() => setError(null)} />
          </div>
        )}

        {/* Messages */}
        <div className="flex-1 overflow-hidden">
          <MessageList
            ref={messagesEndRef}
            messages={messages}
            isLoading={isProcessing && status !== 'thinking'}
          />
        </div>

        {/* Quick Actions - shown when no messages */}
        {messages.length === 0 && !isProcessing && (
          <div className="mx-auto w-full max-w-2xl px-4 pb-6">
            <QuickActions onSelect={handleQuickAction} />
          </div>
        )}

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

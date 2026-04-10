import * as React from 'react';
import { Plus, Trash, ChatCircle } from '@phosphor-icons/react';
import { Button } from '@/components/ui/Button';
import { useChatStore } from '@/stores/chatStore';
import type { ConnectionStatus } from '@/types';
import { cn } from '@/lib/utils';

interface SidebarProps {
  onNewChat: () => void;
  isCollapsed?: boolean;
  onToggleCollapse?: () => void;
}

function ConnectionDot({ status }: { status: ConnectionStatus }) {
  const colors = {
    connected: 'bg-foreground',
    disconnected: 'bg-muted-foreground',
    connecting: 'bg-muted-foreground',
    error: 'bg-foreground',
  };

  return (
    <span className={cn('h-1.5 w-1.5 rounded-full', colors[status])} />
  );
}

interface ConversationItemProps {
  id: string;
  title: string;
  isActive: boolean;
  onSelect: (id: string) => void;
  onDelete: (e: React.MouseEvent, id: string) => void;
}

function ConversationItem({
  id,
  title,
  isActive,
  onSelect,
  onDelete,
}: ConversationItemProps) {
  return (
    <button
      onClick={() => onSelect(id)}
      className={cn(
        'group relative flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm',
        'transition-colors',
        isActive
          ? 'bg-foreground text-background'
          : 'text-foreground hover:bg-muted'
      )}
    >
      <ChatCircle
        className={cn('h-3.5 w-3.5 shrink-0')}
        weight={isActive ? 'fill' : 'regular'}
      />
      <span className="flex-1 truncate pr-6">{title}</span>
      <button
        onClick={(e) => onDelete(e, id)}
        className={cn(
          'absolute right-1 rounded p-1',
          'opacity-0 transition-opacity',
          'hover:bg-background/10 group-hover:opacity-100'
        )}
        aria-label="Delete conversation"
      >
        <Trash className="h-3 w-3" />
      </button>
    </button>
  );
}

export const Sidebar = React.memo(function Sidebar({
  onNewChat,
}: SidebarProps) {
  const { conversations, currentConversationId, setCurrentConversation, removeConversation, connectionStatus } = useChatStore();
  const [isMobileOpen, setIsMobileOpen] = React.useState(false);

  const handleSelectConversation = React.useCallback(
    (id: string) => {
      setCurrentConversation(id);
      setIsMobileOpen(false);
    },
    [setCurrentConversation]
  );

  const handleDelete = React.useCallback(
    (e: React.MouseEvent, id: string) => {
      e.stopPropagation();
      removeConversation(id);
    },
    [removeConversation]
  );

  return (
    <>
      {/* Mobile overlay */}
      {isMobileOpen && (
        <div
          className="fixed inset-0 z-40 bg-background/80 lg:hidden"
          onClick={() => setIsMobileOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={cn(
          'flex h-full w-64 flex-col border-r border-border',
          'bg-background',
          'fixed left-0 top-0 z-50 lg:relative',
          !isMobileOpen && '-translate-x-full lg:translate-x-0'
        )}
      >
        {/* Header */}
        <div className="flex h-14 items-center justify-between border-b border-border px-3">
          <div className="flex items-center gap-2">
            <span className="font-medium text-sm">Claude Code</span>
          </div>
        </div>

        {/* New Chat Button */}
        <div className="p-2">
          <Button
            onClick={onNewChat}
            className="w-full justify-start gap-2 rounded-md bg-foreground text-background hover:bg-foreground/90"
          >
            <Plus className="h-4 w-4 shrink-0" />
            <span>New Chat</span>
          </Button>
        </div>

        {/* Conversation List */}
        <div className="flex-1 overflow-y-auto px-2 py-1">
          {conversations.map((conversation) => (
            <ConversationItem
              key={conversation.id}
              id={conversation.id}
              title={conversation.title || 'New Chat'}
              isActive={currentConversationId === conversation.id}
              onSelect={handleSelectConversation}
              onDelete={handleDelete}
            />
          ))}

          {conversations.length === 0 && (
            <div className="px-2 py-8 text-center">
              <p className="text-xs text-muted-foreground">
                No conversations
              </p>
            </div>
          )}
        </div>

        {/* Footer - Connection Status */}
        <div className="border-t border-border p-3">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <ConnectionDot status={connectionStatus} />
            <span className="capitalize">{connectionStatus}</span>
          </div>
        </div>
      </aside>

      {/* Mobile toggle */}
      <Button
        variant="outline"
        size="sm"
        onClick={() => setIsMobileOpen(true)}
        className="fixed left-4 top-3 z-30 lg:hidden h-8 w-8 p-0"
        aria-label="Open sidebar"
      >
        <ChatCircle className="h-4 w-4" />
      </Button>
    </>
  );
});

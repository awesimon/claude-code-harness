import * as React from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { SidebarSimple, Plus, Trash, ChatCircle } from '@phosphor-icons/react';
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
  const shouldReduceMotion = useReducedMotion();

  const colors = {
    connected: 'bg-emerald-500',
    disconnected: 'bg-muted-foreground',
    connecting: 'bg-amber-500',
    error: 'bg-destructive',
  };

  return (
    <span
      className={cn(
        'h-2 w-2 rounded-full',
        colors[status],
        status === 'connecting' && !shouldReduceMotion && 'animate-pulse'
      )}
      aria-hidden="true"
    />
  );
}

export const Sidebar = React.memo(function Sidebar({
  onNewChat,
  isCollapsed = false,
  onToggleCollapse,
}: SidebarProps) {
  const { conversations, currentConversationId, setCurrentConversation, removeConversation, connectionStatus } = useChatStore();
  const [isMobileOpen, setIsMobileOpen] = React.useState(false);
  const [deletingId, setDeletingId] = React.useState<string | null>(null);
  const shouldReduceMotion = useReducedMotion();

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
      setDeletingId(id);
      // Delay removal for animation
      setTimeout(() => {
        removeConversation(id);
        setDeletingId(null);
      }, 200);
    },
    [removeConversation]
  );

  return (
    <>
      {/* Mobile overlay */}
      <AnimatePresence>
        {isMobileOpen && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm lg:hidden"
            onClick={() => setIsMobileOpen(false)}
          />
        )}
      </AnimatePresence>

      {/* Sidebar */}
      <motion.aside
        initial={false}
        animate={{
          width: isCollapsed ? 72 : 280,
          x: isMobileOpen ? 0 : undefined,
        }}
        transition={{ type: 'spring', stiffness: 400, damping: 30 }}
        className={cn(
          'flex h-full flex-col border-r border-white/5 bg-card/50',
          'fixed left-0 top-0 z-50 lg:relative',
          !isMobileOpen && '-translate-x-full lg:translate-x-0'
        )}
      >
        {/* Header */}
        <div className="flex h-14 items-center justify-between border-b border-white/5 px-3">
          {!isCollapsed && (
            <motion.div
              initial={{ opacity: 0, x: -10 }}
              animate={{ opacity: 1, x: 0 }}
              className="flex items-center gap-3"
            >
              <div className="flex h-8 w-8 items-center justify-center rounded-xl bg-primary/20 text-primary">
                <ChatCircle className="h-4 w-4" weight="fill" />
              </div>
              <span className="font-medium tracking-tight text-foreground">Claude Code</span>
            </motion.div>
          )}
          <Button
            variant="ghost"
            size="icon"
            onClick={onToggleCollapse}
            className={cn('shrink-0', isCollapsed && 'mx-auto')}
            aria-label={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            <SidebarSimple className="h-4 w-4" />
          </Button>
        </div>

        {/* New Chat Button */}
        <div className={cn('p-3', isCollapsed && 'px-2')}>
          <Button
            onClick={onNewChat}
            className={cn(
              'w-full gap-2 transition-all',
              isCollapsed && 'px-2'
            )}
          >
            <Plus className="h-4 w-4 shrink-0" weight="bold" />
            {!isCollapsed && <span>New Chat</span>}
          </Button>
        </div>

        {/* Conversation List */}
        <div className="flex-1 overflow-y-auto p-2 scrollbar-thin">
          <AnimatePresence mode="popLayout">
            {conversations.map((conversation, index) => (
              <motion.div
                key={conversation.id}
                layout={!shouldReduceMotion}
                initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 10 }}
                animate={{ opacity: deletingId === conversation.id ? 0 : 1, y: 0 }}
                exit={{ opacity: 0, scale: 0.95 }}
                transition={{
                  delay: shouldReduceMotion ? 0 : index * 0.05,
                  duration: 0.2,
                }}
              >
                <button
                  onClick={() => handleSelectConversation(conversation.id)}
                  className={cn(
                    'group relative flex w-full items-center rounded-xl px-3 py-2.5 text-left text-sm transition-all duration-200',
                    'hover:bg-white/5',
                    currentConversationId === conversation.id
                      ? 'bg-white/10 text-foreground'
                      : 'text-muted-foreground hover:text-foreground',
                    isCollapsed && 'justify-center px-2'
                  )}
                >
                  <ChatCircle
                    className={cn(
                      'shrink-0',
                      isCollapsed ? 'h-5 w-5' : 'mr-2 h-4 w-4',
                      currentConversationId === conversation.id && 'text-primary'
                    )}
                    weight={currentConversationId === conversation.id ? 'fill' : 'regular'}
                  />
                  {!isCollapsed && (
                    <>
                      <span className="flex-1 truncate pr-6">{conversation.title}</span>
                      <button
                        onClick={(e) => handleDelete(e, conversation.id)}
                        className="absolute right-2 rounded p-1 opacity-0 transition-opacity hover:bg-white/10 group-hover:opacity-100"
                        aria-label="Delete conversation"
                      >
                        <Trash className="h-3 w-3 text-muted-foreground hover:text-destructive" />
                      </button>
                    </>
                  )}
                </button>
              </motion.div>
            ))}
          </AnimatePresence>

          {conversations.length === 0 && !isCollapsed && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="px-3 py-8 text-center"
            >
              <p className="text-xs text-muted-foreground">
                No conversations yet
              </p>
              <p className="mt-1 text-[10px] text-muted-foreground/50">
                Start a new chat to begin
              </p>
            </motion.div>
          )}
        </div>

        {/* Footer - Connection Status */}
        <div className="border-t border-white/5 p-3">
          <div
            className={cn(
              'flex items-center gap-2 text-xs text-muted-foreground',
              isCollapsed && 'justify-center'
            )}
          >
            <ConnectionDot status={connectionStatus} />
            {!isCollapsed && (
              <span className="capitalize">{connectionStatus}</span>
            )}
          </div>
        </div>
      </motion.aside>

      {/* Mobile toggle */}
      <Button
        variant="ghost"
        size="icon"
        onClick={() => setIsMobileOpen(true)}
        className="fixed left-4 top-4 z-30 lg:hidden glass-strong"
        aria-label="Open sidebar"
      >
        <SidebarSimple className="h-4 w-4" />
      </Button>
    </>
  );
});

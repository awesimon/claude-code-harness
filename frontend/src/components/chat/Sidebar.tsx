import * as React from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import { SidebarSimple, Plus, Trash, ChatCircle, Sparkle } from '@phosphor-icons/react';
import { Button } from '@/components/ui/Button';
import { MagneticButton } from '@/components/ui/MagneticButton';
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

  const glowColors = {
    connected: 'shadow-emerald-500/50',
    disconnected: 'shadow-muted-foreground/30',
    connecting: 'shadow-amber-500/50',
    error: 'shadow-destructive/50',
  };

  return (
    <span className="relative flex h-2 w-2">
      <span
        className={cn(
          'absolute inline-flex h-full w-full animate-ping rounded-full opacity-75',
          colors[status],
          status === 'connecting' && !shouldReduceMotion ? 'animate-ping' : 'opacity-0'
        )}
        aria-hidden="true"
      />
      <span
        className={cn(
          'relative inline-flex h-2 w-2 rounded-full shadow-lg',
          colors[status],
          glowColors[status]
        )}
        aria-hidden="true"
      />
    </span>
  );
}

// Animated conversation item component
interface ConversationItemProps {
  id: string;
  title: string;
  isActive: boolean;
  isDeleting: boolean;
  isCollapsed: boolean;
  onSelect: (id: string) => void;
  onDelete: (e: React.MouseEvent, id: string) => void;
  index: number;
}

function ConversationItem({
  id,
  title,
  isActive,
  isDeleting,
  isCollapsed,
  onSelect,
  onDelete,
  index,
}: ConversationItemProps) {
  const shouldReduceMotion = useReducedMotion();

  if (isCollapsed) {
    return (
      <motion.button
        layout={!shouldReduceMotion}
        onClick={() => onSelect(id)}
        whileHover={shouldReduceMotion ? {} : { scale: 1.05 }}
        whileTap={{ scale: 0.95 }}
        className={cn(
          'flex h-10 w-10 items-center justify-center rounded-xl transition-all duration-200',
          'mx-auto',
          isActive
            ? 'bg-primary/20 text-primary shadow-lg shadow-primary/10'
            : 'bg-white/5 text-muted-foreground hover:bg-white/10 hover:text-foreground'
        )}
      >
        <ChatCircle className="h-5 w-5" weight={isActive ? 'fill' : 'regular'} />
      </motion.button>
    );
  }

  return (
    <motion.div
      layout={!shouldReduceMotion}
      initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 10 }}
      animate={{
        opacity: isDeleting ? 0 : 1,
        y: 0,
        x: 0,
      }}
      exit={{ opacity: 0, scale: 0.95, x: -20 }}
      transition={{
        delay: shouldReduceMotion ? 0 : index * 0.03,
        duration: 0.2,
        ease: [0.22, 1, 0.36, 1],
      }}
    >
      <motion.button
        onClick={() => onSelect(id)}
        whileHover={shouldReduceMotion ? {} : { x: 4 }}
        className={cn(
          'group relative flex w-full items-center gap-3 rounded-xl px-3 py-2.5 text-left text-sm',
          'transition-all duration-200',
          isActive
            ? 'bg-gradient-to-r from-primary/10 to-transparent text-foreground'
            : 'text-muted-foreground hover:bg-white/5 hover:text-foreground'
        )}
      >
        {/* Active indicator */}
        <motion.div
          className="absolute left-0 top-1/2 h-6 w-0.5 -translate-y-1/2 rounded-full bg-primary"
          initial={{ scaleY: 0, opacity: 0 }}
          animate={{
            scaleY: isActive ? 1 : 0,
            opacity: isActive ? 1 : 0,
          }}
          transition={{ duration: 0.2 }}
        />

        <ChatCircle
          className={cn(
            'shrink-0 h-4 w-4 transition-colors duration-200',
            isActive ? 'text-primary' : 'text-muted-foreground/50'
          )}
          weight={isActive ? 'fill' : 'regular'}
        />

        <span className="flex-1 truncate pr-8 font-medium">{title}</span>

        {/* Delete button */}
        <motion.button
          onClick={(e) => onDelete(e, id)}
          className={cn(
            'absolute right-2 rounded-lg p-1.5',
            'bg-white/0 opacity-0 transition-all duration-200',
            'hover:bg-destructive/10 group-hover:opacity-100'
          )}
          whileHover={{ scale: 1.1 }}
          whileTap={{ scale: 0.9 }}
          aria-label="Delete conversation"
        >
          <Trash className="h-3.5 w-3.5 text-muted-foreground hover:text-destructive transition-colors" />
        </motion.button>
      </motion.button>
    </motion.div>
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
      setTimeout(() => {
        removeConversation(id);
        setDeletingId(null);
      }, 200);
    },
    [removeConversation]
  );

  return (
    <>
      {/* Mobile overlay with improved animation */}
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

      {/* Sidebar with glass effect */}
      <motion.aside
        initial={false}
        animate={{
          width: isCollapsed ? 80 : 280,
          x: isMobileOpen ? 0 : undefined,
        }}
        transition={{
          type: 'spring',
          stiffness: shouldReduceMotion ? 500 : 300,
          damping: shouldReduceMotion ? 50 : 30,
          mass: 0.8,
        }}
        className={cn(
          'flex h-full flex-col border-r',
          'bg-gradient-to-b from-card/90 via-card/80 to-card/90 backdrop-blur-xl',
          'border-white/5',
          'fixed left-0 top-0 z-50 lg:relative',
          !isMobileOpen && '-translate-x-full lg:translate-x-0'
        )}
      >
        {/* Header with logo */}
        <div className="flex h-16 items-center justify-between border-b border-white/5 px-4">
          <AnimatePresence mode="wait">
            {!isCollapsed && (
              <motion.div
                key="logo"
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -20 }}
                transition={{ duration: 0.2, ease: [0.22, 1, 0.36, 1] }}
                className="flex items-center gap-3"
              >
                <motion.div
                  className="flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-primary/30 to-primary/10 text-primary shadow-lg shadow-primary/10"
                  whileHover={shouldReduceMotion ? {} : { scale: 1.05, rotate: 5 }}
                  transition={{ type: 'spring', stiffness: 400, damping: 25 }}
                >
                  <Sparkle className="h-4 w-4" weight="fill" />
                </motion.div>
                <span className="font-semibold tracking-tight text-foreground">
                  Claude Code
                </span>
              </motion.div>
            )}
          </AnimatePresence>

          <Button
            variant="ghost"
            size="icon"
            onClick={onToggleCollapse}
            className={cn(
              'shrink-0 rounded-xl transition-all duration-200',
              isCollapsed && 'mx-auto',
              'hover:bg-white/10'
            )}
            aria-label={isCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            <motion.div
              animate={{ rotate: isCollapsed ? 180 : 0 }}
              transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
            >
              <SidebarSimple className="h-4 w-4" />
            </motion.div>
          </Button>
        </div>

        {/* New Chat Button with magnetic effect */}
        <div className={cn('p-4', isCollapsed && 'px-2')}>
          <AnimatePresence mode="wait">
            {isCollapsed ? (
              <motion.div
                key="new-chat-collapsed"
                initial={{ opacity: 0, scale: 0.8 }}
                animate={{ opacity: 1, scale: 1 }}
                exit={{ opacity: 0, scale: 0.8 }}
                transition={{ duration: 0.2 }}
              >
                <MagneticButton strength={0.3}>
                  <Button
                    onClick={onNewChat}
                    size="icon"
                    className="h-10 w-10 rounded-xl bg-primary shadow-lg shadow-primary/25 hover:shadow-primary/40 transition-all duration-300"
                  >
                    <Plus className="h-5 w-5" weight="bold" />
                  </Button>
                </MagneticButton>
              </motion.div>
            ) : (
              <motion.div
                key="new-chat-expanded"
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -20 }}
                transition={{ duration: 0.2 }}
              >
                <MagneticButton strength={0.15}>
                  <Button
                    onClick={onNewChat}
                    className="w-full gap-2 rounded-xl bg-primary shadow-lg shadow-primary/25 hover:shadow-primary/40 transition-all duration-300"
                  >
                    <Plus className="h-4 w-4 shrink-0" weight="bold" />
                    <span>New Chat</span>
                  </Button>
                </MagneticButton>
              </motion.div>
            )}
          </AnimatePresence>
        </div>

        {/* Conversation List with improved animations */}
        <div className="flex-1 overflow-y-auto px-2 py-2 scrollbar-thin">
          <AnimatePresence mode="popLayout">
            {conversations.map((conversation, index) => (
              <ConversationItem
                key={conversation.id}
                id={conversation.id}
                title={conversation.title}
                isActive={currentConversationId === conversation.id}
                isDeleting={deletingId === conversation.id}
                isCollapsed={isCollapsed}
                onSelect={handleSelectConversation}
                onDelete={handleDelete}
                index={index}
              />
            ))}
          </AnimatePresence>

          {conversations.length === 0 && !isCollapsed && (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              className="px-4 py-12 text-center"
            >
              <motion.div
                className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-2xl bg-white/5"
                animate={!shouldReduceMotion ? {
                  y: [0, -5, 0],
                } : {}}
                transition={{
                  duration: 3,
                  repeat: Infinity,
                  ease: 'easeInOut',
                }}
              >
                <ChatCircle className="h-5 w-5 text-muted-foreground/50" />
              </motion.div>
              <p className="text-sm font-medium text-muted-foreground">
                No conversations yet
              </p>
              <p className="mt-1 text-xs text-muted-foreground/50">
                Start a new chat to begin
              </p>
            </motion.div>
          )}
        </div>

        {/* Footer - Connection Status with glass effect */}
        <div className="border-t border-white/5 bg-white/[0.02] backdrop-blur-sm p-4">
          <div
            className={cn(
              'flex items-center gap-3 text-xs',
              isCollapsed && 'justify-center'
            )}
          >
            <ConnectionDot status={connectionStatus} />
            {!isCollapsed && (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="flex flex-col"
              >
                <span className="font-medium capitalize text-muted-foreground">
                  {connectionStatus}
                </span>
                <span className="text-[10px] text-muted-foreground/50">
                  Claude 3.5 Sonnet
                </span>
              </motion.div>
            )}
          </div>
        </div>
      </motion.aside>

      {/* Mobile toggle */}
      <motion.div
        initial={{ opacity: 0, x: -20 }}
        animate={{ opacity: 1, x: 0 }}
        transition={{ delay: 0.5 }}
      >
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setIsMobileOpen(true)}
          className="fixed left-4 top-4 z-30 rounded-xl bg-card/80 backdrop-blur-xl border border-white/10 shadow-lg lg:hidden"
          aria-label="Open sidebar"
        >
          <SidebarSimple className="h-4 w-4" />
        </Button>
      </motion.div>
    </>
  );
});

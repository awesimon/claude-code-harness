import * as React from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import {
  ChatCircleText,
  MagnifyingGlass,
  X,
  ArrowDown,
  Sparkle,
} from '@phosphor-icons/react';
import { Message } from './Message';
import type { Message as MessageType } from '@/types';
import { cn } from '@/lib/utils';

// Props interface
interface MessageListProps {
  messages: MessageType[];
  isLoading?: boolean;
  className?: string;
  onCopyMessage?: (content: string) => void;
  emptyStateTitle?: string;
  emptyStateDescription?: string;
  enableSearch?: boolean;
}

// Search filter type
type SearchFilter = {
  query: string;
  role: 'all' | 'user' | 'assistant';
};

// Highlight matched text
function highlightText(text: string, query: string): string {
  if (!query.trim()) return text;
  const regex = new RegExp(`(${escapeRegExp(query)})`, 'gi');
  return text.replace(regex, '<mark class="bg-yellow-500/30 text-yellow-200">$1</mark>');
}

function escapeRegExp(string: string): string {
  return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

// Search input component
const SearchBar = React.memo(function SearchBar({
  filter,
  onFilterChange,
  resultCount,
}: {
  filter: SearchFilter;
  onFilterChange: (filter: SearchFilter) => void;
  resultCount: number;
}) {
  const inputRef = React.useRef<HTMLInputElement>(null);

  const handleClear = React.useCallback(() => {
    onFilterChange({ ...filter, query: '' });
    inputRef.current?.focus();
  }, [filter, onFilterChange]);

  return (
    <div className="flex items-center gap-2 px-4 py-2 glass border-b border-white/5">
      <div className="relative flex-1">
        <MagnifyingGlass className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
        <input
          ref={inputRef}
          type="text"
          placeholder="Search messages..."
          value={filter.query}
          onChange={(e) => onFilterChange({ ...filter, query: e.target.value })}
          className={cn(
            'w-full pl-9 pr-8 py-1.5 text-sm bg-white/5 rounded-lg',
            'border border-transparent focus:border-primary/50',
            'text-foreground placeholder:text-muted-foreground',
            'transition-colors outline-none'
          )}
        />
        {filter.query && (
          <button
            onClick={handleClear}
            className="absolute right-2 top-1/2 -translate-y-1/2 p-0.5 rounded hover:bg-white/10 text-muted-foreground"
          >
            <X className="h-3 w-3" />
          </button>
        )}
      </div>
      <select
        value={filter.role}
        onChange={(e) => onFilterChange({ ...filter, role: e.target.value as SearchFilter['role'] })}
        className={cn(
          'px-2 py-1.5 text-sm bg-white/5 rounded-lg',
          'border border-transparent focus:border-primary/50',
          'text-foreground outline-none cursor-pointer'
        )}
      >
        <option value="all">All</option>
        <option value="user">User</option>
        <option value="assistant">Assistant</option>
      </select>
      {filter.query && (
        <span className="text-xs text-muted-foreground whitespace-nowrap">
          {resultCount} result{resultCount !== 1 ? 's' : ''}
        </span>
      )}
    </div>
  );
});

SearchBar.displayName = 'SearchBar';

// Scroll to bottom button
const ScrollToBottomButton = React.memo(function ScrollToBottomButton({
  onClick,
  visible,
}: {
  onClick: () => void;
  visible: boolean;
}) {
  const shouldReduceMotion = useReducedMotion();

  return (
    <AnimatePresence>
      {visible && (
        <motion.button
          initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={shouldReduceMotion ? { opacity: 0 } : { opacity: 0, y: 10 }}
          transition={{ duration: 0.2 }}
          onClick={onClick}
          className={cn(
            'absolute bottom-4 right-4 z-10',
            'flex items-center gap-2 px-3 py-2 rounded-full',
            'bg-primary text-primary-foreground shadow-lg',
            'hover:bg-primary/90 active:scale-95',
            'transition-all duration-200'
          )}
          aria-label="Scroll to bottom"
        >
          <ArrowDown className="h-4 w-4" />
          <span className="text-sm font-medium">New messages</span>
        </motion.button>
      )}
    </AnimatePresence>
  );
});

ScrollToBottomButton.displayName = 'ScrollToBottomButton';

// Typing indicator component - isolated for performance
const TypingIndicator = React.memo(function TypingIndicator() {
  const shouldReduceMotion = useReducedMotion();

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      className="flex gap-3"
    >
      {/* Avatar */}
      <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full glass-strong bg-primary/20">
        <Sparkle className="h-4 w-4 text-primary animate-pulse" weight="fill" />
      </div>

      {/* Typing dots */}
      <div className="glass rounded-2xl px-4 py-3">
        <div className="flex items-center gap-1.5">
          {[0, 1, 2].map((i) => (
            <motion.div
              key={i}
              animate={
                shouldReduceMotion
                  ? {}
                  : {
                      scale: [1, 1.3, 1],
                      opacity: [0.4, 1, 0.4],
                    }
              }
              transition={{
                repeat: Infinity,
                duration: 1.2,
                delay: i * 0.15,
                ease: 'easeInOut',
              }}
              className="h-2 w-2 rounded-full bg-primary"
            />
          ))}
        </div>
      </div>
    </motion.div>
  );
});

TypingIndicator.displayName = 'TypingIndicator';

// Empty state component
const EmptyState = React.memo(function EmptyState({
  shouldReduceMotion,
  title,
  description,
}: {
  shouldReduceMotion: boolean | null;
  title: string;
  description: string;
}) {
  const features = [
    'File Operations',
    'Code Editing',
    'Web Search',
    'Command Execution',
  ];

  return (
    <div className="flex h-full flex-col items-center justify-center px-4">
      <motion.div
        initial={
          shouldReduceMotion
            ? { opacity: 1 }
            : { opacity: 0, scale: 0.9, y: 20 }
        }
        animate={{ opacity: 1, scale: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
        className="text-center"
      >
        {/* Logo Icon */}
        <motion.div
          className="mx-auto mb-6 flex h-16 w-16 items-center justify-center rounded-2xl glass-strong bg-primary/20"
          whileHover={shouldReduceMotion ? {} : { scale: 1.05, rotate: 5 }}
          transition={{ type: 'spring', stiffness: 400, damping: 25 }}
        >
          <ChatCircleText
            className="h-8 w-8 text-primary"
            weight="duotone"
          />
        </motion.div>

        <h2 className="mb-2 text-3xl font-semibold tracking-tight text-foreground">
          {title}
        </h2>
        <p className="max-w-md mx-auto text-muted-foreground leading-relaxed">
          {description}
        </p>

        {/* Feature badges */}
        <div className="mt-8 flex flex-wrap justify-center gap-2">
          {features.map((feature, i) => (
            <motion.span
              key={feature}
              initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.2 + i * 0.1 }}
              className="px-3 py-1.5 text-xs glass rounded-full text-muted-foreground hover:text-foreground hover:bg-white/10 transition-colors cursor-default"
            >
              {feature}
            </motion.span>
          ))}
        </div>

        {/* Quick tips */}
        <motion.div
          initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 0.6 }}
          className="mt-10 text-sm text-muted-foreground"
        >
          <p className="mb-2">Try asking:</p>
          <div className="space-y-1.5">
            {[
              'Explain this codebase to me',
              'Refactor the main function',
              'Search for TODO comments',
              'Run the test suite',
            ].map((example) => (
              <p
                key={example}
                className="text-xs opacity-60 hover:opacity-100 transition-opacity cursor-pointer"
              >
                &ldquo;{example}&rdquo;
              </p>
            ))}
          </div>
        </motion.div>
      </motion.div>
    </div>
  );
});

EmptyState.displayName = 'EmptyState';

// Filter messages based on search query
function useFilteredMessages(
  messages: MessageType[],
  filter: SearchFilter
): MessageType[] {
  return React.useMemo(() => {
    if (!filter.query.trim() && filter.role === 'all') {
      return messages;
    }

    return messages.filter((msg) => {
      // Role filter
      if (filter.role !== 'all' && msg.role !== filter.role) {
        return false;
      }

      // Text search
      if (filter.query.trim()) {
        const query = filter.query.toLowerCase();
        const content = msg.content.toLowerCase();
        return content.includes(query);
      }

      return true;
    });
  }, [messages, filter]);
}

// Main MessageList component
export const MessageList = React.forwardRef<HTMLDivElement, MessageListProps>(
  function MessageList(
    {
      messages,
      isLoading,
      className,
      onCopyMessage,
      emptyStateTitle = 'Claude Code',
      emptyStateDescription = 'A powerful AI assistant with advanced tool capabilities. Read files, write code, run commands, and more.',
      enableSearch = true,
    },
    ref
  ) {
    const scrollRef = React.useRef<HTMLDivElement>(null);
    const shouldReduceMotion = useReducedMotion();
    const [showScrollButton, setShowScrollButton] = React.useState(false);
    const [filter, setFilter] = React.useState<SearchFilter>({
      query: '',
      role: 'all',
    });

    const filteredMessages = useFilteredMessages(messages, filter);
    const isSearching = filter.query.trim() !== '' || filter.role !== 'all';

    // Handle scroll events to show/hide scroll to bottom button
    const handleScroll = React.useCallback(() => {
      if (!scrollRef.current) return;

      const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
      const isNearBottom = scrollHeight - scrollTop - clientHeight < 100;
      setShowScrollButton(!isNearBottom);
    }, []);

    // Auto-scroll to bottom with smooth behavior
    const scrollToBottom = React.useCallback(
      (behavior: ScrollBehavior = 'smooth') => {
        if (scrollRef.current) {
          scrollRef.current.scrollTo({
            top: scrollRef.current.scrollHeight,
            behavior: shouldReduceMotion ? 'auto' : behavior,
          });
        }
      },
      [shouldReduceMotion]
    );

    // Auto-scroll when new messages arrive (if already near bottom)
    React.useEffect(() => {
      if (!scrollRef.current || isSearching) return;

      const { scrollTop, scrollHeight, clientHeight } = scrollRef.current;
      const isNearBottom = scrollHeight - scrollTop - clientHeight < 200;

      if (isNearBottom || messages.length <= 1) {
        scrollToBottom('smooth');
      }
    }, [messages, isLoading, scrollToBottom, isSearching]);

    // Set up scroll listener
    React.useEffect(() => {
      const element = scrollRef.current;
      if (!element) return;

      element.addEventListener('scroll', handleScroll);
      handleScroll(); // Initial check

      return () => element.removeEventListener('scroll', handleScroll);
    }, [handleScroll]);

    // Empty state
    if (messages.length === 0) {
      return (
        <div
          ref={ref}
          className={cn('h-full', className)}
          role="region"
          aria-label="Chat messages"
        >
          <EmptyState
            shouldReduceMotion={shouldReduceMotion}
            title={emptyStateTitle}
            description={emptyStateDescription}
          />
        </div>
      );
    }

    return (
      <div className={cn('flex flex-col h-full', className)}>
        {/* Search bar */}
        {enableSearch && messages.length > 5 && (
          <SearchBar
            filter={filter}
            onFilterChange={setFilter}
            resultCount={filteredMessages.length}
          />
        )}

        {/* Messages container */}
        <div
          ref={(node) => {
            (scrollRef as React.MutableRefObject<HTMLDivElement | null>).current =
              node;
            if (typeof ref === 'function') {
              ref(node);
            } else if (ref) {
              (ref as React.MutableRefObject<HTMLDivElement | null>).current =
                node;
            }
          }}
          className="flex-1 overflow-y-auto px-4 py-6 scrollbar-thin relative"
          aria-live="polite"
          aria-atomic="false"
          role="log"
          aria-label="Chat messages"
        >
          <div className="mx-auto max-w-3xl space-y-6">
            {isSearching && filteredMessages.length === 0 ? (
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                className="text-center py-12"
              >
                <p className="text-muted-foreground">
                  No messages match your search.
                </p>
                <button
                  onClick={() => setFilter({ query: '', role: 'all' })}
                  className="mt-2 text-sm text-primary hover:underline"
                >
                  Clear search
                </button>
              </motion.div>
            ) : (
              <AnimatePresence mode="popLayout" initial={false}>
                {filteredMessages.map((message, index) => (
                  <motion.div
                    key={message.id}
                    layout
                    initial={
                      shouldReduceMotion
                        ? { opacity: 1 }
                        : { opacity: 0, y: 20 }
                    }
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, scale: 0.95 }}
                    transition={{
                      duration: 0.3,
                      delay: shouldReduceMotion
                        ? 0
                        : Math.min(index * 0.05, 0.3),
                      ease: [0.16, 1, 0.3, 1],
                    }}
                  >
                    <Message
                      message={message}
                      isLast={index === filteredMessages.length - 1}
                      onCopy={onCopyMessage}
                    />
                  </motion.div>
                ))}
              </AnimatePresence>
            )}

            {/* Loading/Typing indicator */}
            <AnimatePresence>
              {isLoading && !isSearching && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                >
                  <TypingIndicator />
                </motion.div>
              )}
            </AnimatePresence>

            {/* Bottom spacer for scrolling */}
            <div className="h-4" aria-hidden="true" />
          </div>

          {/* Scroll to bottom button */}
          <ScrollToBottomButton
            onClick={() => scrollToBottom('smooth')}
            visible={showScrollButton}
          />
        </div>
      </div>
    );
  }
);

MessageList.displayName = 'MessageList';

export default MessageList;

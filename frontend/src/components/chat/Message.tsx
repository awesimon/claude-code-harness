import * as React from 'react';
import { motion, useReducedMotion } from 'framer-motion';
import { User, Robot, Copy, Check, FileText } from '@phosphor-icons/react';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import hljs from 'highlight.js';
import 'highlight.js/styles/github-dark.min.css';
import type { Message as MessageType } from '@/types';
import { cn } from '@/lib/utils';
import { ToolCall } from '@/components/tools/ToolCall';
import { ToolResult } from '@/components/tools/ToolResult';

// Configure marked for security and features
marked.setOptions({
  breaks: true,
  gfm: true,
  mangle: false,
  sanitize: false, // We use DOMPurify instead
} as any);

// Custom renderer for better code blocks
const renderer = new marked.Renderer();
renderer.code = (code: string, language?: string) => {
  const validLang = language && hljs.getLanguage(language) ? language : 'plaintext';
  const highlighted = hljs.highlight(code, { language: validLang }).value;
  return `
    <div class="code-block-wrapper">
      <div class="code-block-header">
        <span class="code-language">${validLang}</span>
        <button class="code-copy-btn" data-code="${encodeURIComponent(code)}" aria-label="Copy code">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
            <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
          </svg>
          Copy
        </button>
      </div>
      <pre><code class="hljs language-${validLang}">${highlighted}</code></pre>
    </div>
  `;
};

renderer.codespan = (code: string) => {
  return `<code class="inline-code">${code}</code>`;
};

marked.use({ renderer });

// Props interface
interface MessageProps {
  message: MessageType;
  isLast?: boolean;
  onCopy?: (content: string) => void;
}

// Hook for copying text to clipboard
function useCopyToClipboard() {
  const [copied, setCopied] = React.useState(false);

  const copy = React.useCallback(async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
      return true;
    } catch (err) {
      console.error('Failed to copy:', err);
      return false;
    }
  }, []);

  return { copied, copy };
}

// Extract text content from message (for copying)
const extractTextContent = (content: string): string => {
  // Simple HTML tag removal for copy functionality
  return content.replace(/<[^>]*>/g, '');
};

// Memoized message content component for better performance
const MessageContent = React.memo(function MessageContent({
  content,
  isUser,
  contentRef,
}: {
  content: string;
  isUser: boolean;
  contentRef: React.RefObject<HTMLDivElement>;
}) {
  const formattedContent = React.useMemo(() => {
    const rawHtml = marked.parse(content);
    return DOMPurify.sanitize(rawHtml as string, {
      ALLOWED_TAGS: [
        'p', 'br', 'strong', 'em', 'u', 's', 'del', 'ins',
        'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
        'ul', 'ol', 'li',
        'blockquote', 'hr',
        'code', 'pre',
        'a', 'img',
        'table', 'thead', 'tbody', 'tr', 'th', 'td',
        'div', 'span',
      ],
      ALLOWED_ATTR: [
        'href', 'title', 'alt', 'src',
        'class', 'data-code', 'aria-label',
      ],
    });
  }, [content]);

  return (
    <div
      ref={contentRef}
      className={cn(
        'message-content prose prose-invert max-w-none',
        'prose-headings:mb-3 prose-headings:mt-4 prose-headings:font-semibold',
        'prose-p:my-2 prose-p:leading-relaxed',
        'prose-ul:my-2 prose-ol:my-2 prose-li:my-1',
        'prose-blockquote:border-l-2 prose-blockquote:border-primary/50 prose-blockquote:pl-4 prose-blockquote:italic',
        'prose-code:before:content-none prose-code:after:content-none',
        'prose-pre:my-0 prose-pre:p-0',
        'prose-a:text-primary hover:prose-a:underline',
        'prose-table:border-collapse prose-table:w-full',
        'prose-th:border prose-th:border-white/10 prose-th:p-2 prose-th:bg-white/5',
        'prose-td:border prose-td:border-white/10 prose-td:p-2',
        'prose-img:rounded-lg prose-img:max-w-full',
        'prose-hr:border-white/10',
      )}
      dangerouslySetInnerHTML={{ __html: formattedContent }}
    />
  );
});

MessageContent.displayName = 'MessageContent';

// Copy button component
const CopyButton = React.memo(function CopyButton({
  onCopy,
  copied,
  content,
}: {
  onCopy: (text: string) => Promise<boolean>;
  copied: boolean;
  content: string;
}) {
  return (
    <button
      onClick={() => onCopy(content)}
      className={cn(
        'flex items-center gap-1.5 px-2 py-1 rounded-md text-xs',
        'transition-all duration-200',
        'hover:bg-white/10 active:scale-95',
        copied ? 'text-emerald-400' : 'text-muted-foreground hover:text-foreground'
      )}
      aria-label={copied ? 'Copied!' : 'Copy message'}
    >
      {copied ? (
        <>
          <Check className="h-3 w-3" weight="bold" />
          <span>Copied</span>
        </>
      ) : (
        <>
          <Copy className="h-3 w-3" />
          <span>Copy</span>
        </>
      )}
    </button>
  );
});

CopyButton.displayName = 'CopyButton';

// Main Message component
export const Message = React.memo(function Message({
  message,
  isLast = false,
  onCopy,
}: MessageProps) {
  const contentRef = React.useRef<HTMLDivElement>(null);
  const isUser = message.role === 'user';
  const shouldReduceMotion = useReducedMotion();
  const { copied, copy } = useCopyToClipboard();

  // Track expanded states for tool calls and results
  const [expandedTools, setExpandedTools] = React.useState<Set<string>>(new Set());

  const toggleTool = React.useCallback((id: string) => {
    setExpandedTools((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  }, []);

  // Handle copy button clicks for code blocks
  React.useEffect(() => {
    if (!contentRef.current) return;

    const handleCodeCopy = async (e: Event) => {
      const target = e.target as HTMLElement;
      const button = target.closest('.code-copy-btn') as HTMLButtonElement;
      if (!button) return;

      const code = decodeURIComponent(button.dataset.code || '');
      if (code) {
        const success = await copy(code);
        if (success) {
          const originalText = button.innerHTML;
          button.innerHTML = `
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <polyline points="20 6 9 17 4 12"></polyline>
            </svg>
            Copied!
          `;
          button.classList.add('copied');
          setTimeout(() => {
            button.innerHTML = originalText;
            button.classList.remove('copied');
          }, 2000);
        }
      }
    };

    const codeBlocks = contentRef.current.querySelectorAll('.code-copy-btn');
    codeBlocks.forEach((btn) => {
      btn.addEventListener('click', handleCodeCopy);
    });

    return () => {
      codeBlocks.forEach((btn) => {
        btn.removeEventListener('click', handleCodeCopy);
      });
    };
  }, [message.content, copy]);

  // Handle message copy
  const handleCopyMessage = React.useCallback(async () => {
    const text = extractTextContent(message.content);
    const success = await copy(text);
    if (success && onCopy) {
      onCopy(text);
    }
    return success;
  }, [message.content, copy, onCopy]);

  // Determine if message has content to display
  const hasContent = Boolean(message.content?.trim());
  const hasToolCalls = message.toolCalls && message.toolCalls.length > 0;
  const hasToolResults = message.toolResults && message.toolResults.length > 0;

  // Animation variants
  const containerVariants = {
    initial: shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 20 },
    animate: { opacity: 1, y: 0 },
  };

  const avatarVariants = {
    initial: shouldReduceMotion ? { opacity: 1 } : { scale: 0.8, opacity: 0 },
    animate: { scale: 1, opacity: 1 },
  };

  const contentVariants = {
    initial: shouldReduceMotion ? { opacity: 1 } : { opacity: 0, x: isUser ? 20 : -20 },
    animate: { opacity: 1, x: 0 },
  };

  return (
    <motion.div
      variants={containerVariants}
      initial="initial"
      animate="animate"
      transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
      className={cn('group flex gap-3', isUser ? 'flex-row-reverse' : 'flex-row')}
      data-message-id={message.id}
      data-message-role={message.role}
    >
      {/* Avatar - Glass effect */}
      <motion.div
        variants={avatarVariants}
        initial="initial"
        animate="animate"
        transition={{ delay: shouldReduceMotion ? 0 : 0.1 }}
        className={cn(
          'flex h-8 w-8 shrink-0 select-none items-center justify-center rounded-full glass-strong',
          isUser ? 'bg-secondary' : 'bg-primary/20'
        )}
        aria-hidden="true"
      >
        {isUser ? (
          <User className="h-4 w-4 text-foreground" weight="bold" />
        ) : (
          <Robot className="h-4 w-4 text-primary" weight="bold" />
        )}
      </motion.div>

      {/* Content Column */}
      <div className={cn('flex-1 min-w-0', isUser ? 'text-right' : 'text-left')}>
        {/* Tool Calls */}
        {hasToolCalls && (
          <div className="mb-3 space-y-2">
            {message.toolCalls!.map((toolCall) => (
              <ToolCall
                key={toolCall.id}
                toolCall={toolCall}
                isExpanded={expandedTools.has(toolCall.id)}
                onToggle={() => toggleTool(toolCall.id)}
              />
            ))}
          </div>
        )}

        {/* Message Content */}
        {hasContent && (
          <motion.div
            variants={contentVariants}
            initial="initial"
            animate="animate"
            transition={{ delay: shouldReduceMotion ? 0 : 0.15, duration: 0.25 }}
            className={cn(
              'inline-block max-w-[90%] rounded-2xl px-4 py-3 text-left tap-highlight',
              'relative group/content',
              isUser
                ? 'bg-primary text-primary-foreground'
                : 'glass border border-white/10'
            )}
          >
            <MessageContent
              content={message.content}
              isUser={isUser}
              contentRef={contentRef}
            />

            {/* Copy button - shown on hover for assistant messages */}
            {!isUser && (
              <div className="absolute -bottom-6 right-0 opacity-0 transition-opacity duration-200 group-hover/content:opacity-100">
                <CopyButton
                  onCopy={handleCopyMessage}
                  copied={copied}
                  content={extractTextContent(message.content)}
                />
              </div>
            )}
          </motion.div>
        )}

        {/* Tool Results */}
        {hasToolResults && (
          <div className={cn('mt-3 space-y-2', isUser ? 'text-right' : 'text-left')}>
            {message.toolResults!.map((toolResult, index) => (
              <ToolResult
                key={`${toolResult.id}-${index}`}
                toolResult={toolResult}
                isExpanded={expandedTools.has(`result-${toolResult.id}`)}
                onToggle={() => toggleTool(`result-${toolResult.id}`)}
              />
            ))}
          </div>
        )}

        {/* File attachments indicator (if any) */}
        {message.role === 'user' && (
          <div className="mt-1 flex justify-end gap-1">
            {/* Placeholder for future file attachment support */}
          </div>
        )}

        {/* Timestamp */}
        <motion.div
          initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: shouldReduceMotion ? 0 : 0.3 }}
          className={cn(
            'mt-1 text-xs text-muted-foreground',
            !hasContent && !hasToolCalls && 'mt-0'
          )}
        >
          <time dateTime={new Date(message.timestamp).toISOString()}>
            {new Date(message.timestamp).toLocaleTimeString('en-US', {
              hour: '2-digit',
              minute: '2-digit',
            })}
          </time>
        </motion.div>
      </div>
    </motion.div>
  );
});

Message.displayName = 'Message';

export default Message;

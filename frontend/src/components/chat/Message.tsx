import * as React from 'react';
import { User, Robot, Copy, Check } from '@phosphor-icons/react';
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import hljs from 'highlight.js';
import 'highlight.js/styles/github-dark.min.css';
import type { Message as MessageType } from '@/types';
import { cn } from '@/lib/utils';
import { ToolExecution } from '@/components/tools/ToolExecution';
import { ThinkingBlock } from './ThinkingBlock';

// Configure marked for security and features
marked.setOptions({
  breaks: true,
  gfm: true,
  mangle: false,
  sanitize: false,
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

interface MessageProps {
  message: MessageType;
  isLast?: boolean;
  onCopy?: (content: string) => void;
}

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

const extractTextContent = (content: string): string => {
  return content.replace(/<[^>]*>/g, '');
};

const MessageContent = React.memo(function MessageContent({
  content,
  contentRef,
}: {
  content: string;
  contentRef: React.RefObject<HTMLDivElement>;
}) {
  const formattedContent = React.useMemo(() => {
    const maxLength = 50000;
    const truncatedContent = content.length > maxLength
      ? content.slice(0, maxLength) + '\n\n[内容已截断...]'
      : content;

    try {
      const rawHtml = marked.parse(truncatedContent);
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
    } catch (e) {
      return `<p>${truncatedContent.replace(/</g, '&lt;').replace(/>/g, '&gt;')}</p>`;
    }
  }, [content]);

  return (
    <div
      ref={contentRef}
      className="message-content"
      dangerouslySetInnerHTML={{ __html: formattedContent }}
    />
  );
});

MessageContent.displayName = 'MessageContent';

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
        'flex items-center gap-1.5 px-2 py-1 text-xs',
        'transition-colors',
        copied ? 'text-foreground' : 'text-muted-foreground hover:text-foreground'
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

export const Message = React.memo(function Message({
  message,
  isLast = false,
  onCopy,
}: MessageProps) {
  const contentRef = React.useRef<HTMLDivElement>(null);
  const isUser = message.role === 'user';
  const { copied, copy } = useCopyToClipboard();
  const [expandedTools, setExpandedTools] = React.useState<Set<string>>(new Set());
  const [isThinkingExpanded, setIsThinkingExpanded] = React.useState(false);

  // 使用 CSS 变量而不是硬编码颜色
  // 用户消息和助手消息都用浅色背景，保持色调一致
  const userAvatarClass = 'bg-muted text-foreground';
  const assistantAvatarClass = 'bg-muted text-foreground';

  const userBubbleClass = 'bg-muted text-foreground';
  const assistantBubbleClass = 'bg-muted text-foreground';

  const avatarClass = isUser ? userAvatarClass : assistantAvatarClass;
  const bubbleClass = isUser ? userBubbleClass : assistantBubbleClass;

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

  const toggleThinking = React.useCallback(() => {
    setIsThinkingExpanded((prev) => !prev);
  }, []);

  React.useEffect(() => {
    if (!contentRef.current) return;
    const contentElement = contentRef.current;

    const handleCodeCopy = async (e: MouseEvent) => {
      const button = (e.target as HTMLElement).closest('.code-copy-btn') as HTMLButtonElement;
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

    contentElement.addEventListener('click', handleCodeCopy);
    return () => {
      contentElement.removeEventListener('click', handleCodeCopy);
    };
  }, [copy]);

  const handleCopyMessage = React.useCallback(async () => {
    const text = extractTextContent(message.content);
    const success = await copy(text);
    if (success && onCopy) {
      onCopy(text);
    }
    return success;
  }, [message.content, copy, onCopy]);

  const hasContent = Boolean(message.content?.trim());
  const hasToolCalls = message.toolCalls && message.toolCalls.length > 0;
  const hasThinking = Boolean(message.thinking?.trim());

  return (
    <div
      className={cn('group flex gap-3', isUser ? 'flex-row-reverse' : 'flex-row')}
      data-message-id={message.id}
      data-message-role={message.role}
    >
      {/* Avatar */}
      <div
        className={cn(
          'flex h-7 w-7 shrink-0 select-none items-center justify-center rounded-full',
          avatarClass
        )}
        aria-hidden="true"
      >
        {isUser ? (
          <User className="h-3.5 w-3.5" weight="bold" />
        ) : (
          <Robot className="h-3.5 w-3.5" weight="bold" />
        )}
      </div>

      {/* Content Column */}
      <div className={cn('flex-1 min-w-0', isUser ? 'text-right' : 'text-left')}>
        {hasThinking && (
          <ThinkingBlock
            thinking={message.thinking!}
            thinkingTime={message.thinkingTime}
            isExpanded={isThinkingExpanded}
            onToggle={toggleThinking}
          />
        )}

        {/* Message Bubble */}
        {hasContent && (
          <div
            className={cn(
              'inline-block max-w-[85%] rounded-lg px-4 py-3',
              'relative group/content',
              bubbleClass
            )}
          >
            <MessageContent
              content={message.content}
              contentRef={contentRef}
            />

            {!isUser && (
              <div className="absolute -bottom-6 right-0 opacity-0 transition-opacity group-hover/content:opacity-100">
                <CopyButton
                  onCopy={handleCopyMessage}
                  copied={copied}
                  content={extractTextContent(message.content)}
                />
              </div>
            )}
          </div>
        )}

        {/* Tool Executions */}
        {hasToolCalls && (
          <div className="mt-3 space-y-2">
            {message.toolCalls!.map((toolCall) => {
              const toolResult = message.toolResults?.find(
                (result) => result.id === toolCall.id
              );
              return (
                <ToolExecution
                  key={toolCall.id}
                  toolCall={toolCall}
                  toolResult={toolResult}
                  isExpanded={expandedTools.has(toolCall.id)}
                  onToggle={() => toggleTool(toolCall.id)}
                />
              );
            })}
          </div>
        )}

        {/* Timestamp */}
        <div className="mt-1 text-xs text-neutral-500">
          <time dateTime={new Date(message.timestamp).toISOString()}>
            {new Date(message.timestamp).toLocaleTimeString('en-US', {
              hour: '2-digit',
              minute: '2-digit',
            })}
          </time>
        </div>
      </div>
    </div>
  );
});

Message.displayName = 'Message';

export default Message;

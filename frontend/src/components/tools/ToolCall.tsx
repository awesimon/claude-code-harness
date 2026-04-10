import * as React from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import {
  Wrench,
  CaretDown,
  FileText,
  Command,
  Globe,
  MagnifyingGlass,
  Terminal,
  Database,
  Code,
  FolderOpen,
  PencilSimple,
  Copy,
  CheckCircle,
  Spinner,
  Clock,
} from '@phosphor-icons/react';
import type { ToolCall as ToolCallType } from '@/types';
import { cn } from '@/lib/utils';

interface ToolCallProps {
  toolCall: ToolCallType;
  isExpanded?: boolean;
  onToggle?: () => void;
  status?: 'pending' | 'executing' | 'completed' | 'error';
  className?: string;
}

// Tool icon mapping with comprehensive coverage
const toolIcons: Record<string, React.ComponentType<{ className?: string }>> = {
  // File operations
  read_file: FileText,
  write_file: PencilSimple,
  edit_file: PencilSimple,
  file_search: MagnifyingGlass,
  glob: MagnifyingGlass,

  // System operations
  bash: Terminal,
  command: Command,
  exec: Terminal,

  // Search operations
  grep: MagnifyingGlass,
  search: MagnifyingGlass,
  find: MagnifyingGlass,

  // Web operations
  web_search: Globe,
  web_fetch: Globe,
  fetch: Globe,
  http: Globe,

  // Database operations
  db_query: Database,
  database: Database,
  sql: Database,

  // Code operations
  code: Code,
  analyze: Code,
  parse: Code,

  // Directory operations
  list_dir: FolderOpen,
  directory: FolderOpen,
  ls: FolderOpen,
};

// Tool category mapping for grouping
const toolCategories: Record<string, string> = {
  read_file: 'file',
  write_file: 'file',
  edit_file: 'file',
  file_search: 'file',
  glob: 'file',
  bash: 'system',
  command: 'system',
  exec: 'system',
  grep: 'search',
  search: 'search',
  find: 'search',
  web_search: 'web',
  web_fetch: 'web',
  fetch: 'web',
  http: 'web',
  db_query: 'database',
  database: 'database',
  sql: 'database',
  code: 'code',
  analyze: 'code',
  parse: 'code',
  list_dir: 'directory',
  directory: 'directory',
  ls: 'directory',
};

// Category colors - using CSS variables for theming support
const categoryColors: Record<string, string> = {
  file: 'text-[hsl(var(--accent-info))] bg-[hsl(var(--accent-info)/0.2)] border-[hsl(var(--accent-info)/0.2)]',
  system: 'text-purple-400 bg-purple-500/20 border-purple-500/20',
  search: 'text-amber-400 bg-amber-500/20 border-amber-500/20',
  web: 'text-cyan-400 bg-cyan-500/20 border-cyan-500/20',
  database: 'text-[hsl(var(--accent-success))] bg-[hsl(var(--accent-success)/0.2)] border-[hsl(var(--accent-success)/0.2)]',
  code: 'text-pink-400 bg-pink-500/20 border-pink-500/20',
  directory: 'text-orange-400 bg-orange-500/20 border-orange-500/20',
  default: 'text-[hsl(var(--accent))] bg-[hsl(var(--accent)/0.2)] border-[hsl(var(--accent)/0.2)]',
};

interface Parameter {
  key: string;
  value: unknown;
  type: string;
}

function parseParameters(args: Record<string, unknown>): Parameter[] {
  return Object.entries(args).map(([key, value]) => ({
    key,
    value,
    type: typeof value,
  }));
}

function formatValue(value: unknown): string {
  if (value === null) return 'null';
  if (value === undefined) return 'undefined';
  if (typeof value === 'string') return value;
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function getValuePreview(value: unknown, maxLength: number = 50): string {
  const formatted = formatValue(value);
  if (formatted.length <= maxLength) return formatted;
  return formatted.slice(0, maxLength) + '...';
}

export const ToolCall = React.memo(function ToolCall({
  toolCall,
  isExpanded = false,
  onToggle,
  status = 'completed',
  className,
}: ToolCallProps) {
  const shouldReduceMotion = useReducedMotion();
  const [copiedParam, setCopiedParam] = React.useState<string | null>(null);

  const formattedArgs = React.useMemo(() => {
    try {
      return JSON.stringify(toolCall.arguments, null, 2);
    } catch {
      return String(toolCall.arguments);
    }
  }, [toolCall.arguments]);

  const parameters = React.useMemo(
    () => parseParameters(toolCall.arguments),
    [toolCall.arguments]
  );

  // Get appropriate icon
  const IconComponent = toolIcons[toolCall.name] || Wrench;

  // Get category for styling
  const category = toolCategories[toolCall.name] || 'default';
  const colorClasses = categoryColors[category] || categoryColors.default;

  const handleCopyParam = async (key: string, value: unknown) => {
    try {
      await navigator.clipboard.writeText(formatValue(value));
      setCopiedParam(key);
      setTimeout(() => setCopiedParam(null), 2000);
    } catch {
      // Silent fail
    }
  };

  const handleCopyAll = async () => {
    try {
      await navigator.clipboard.writeText(formattedArgs);
    } catch {
      // Silent fail
    }
  };

  const getStatusIcon = () => {
    switch (status) {
      case 'executing':
        return (
          <motion.div
            animate={shouldReduceMotion ? {} : { rotate: 360 }}
            transition={{
              duration: 1,
              repeat: Infinity,
              ease: 'linear',
            }}
          >
            <Spinner className="h-4 w-4 text-accent" weight="bold" />
          </motion.div>
        );
      case 'completed':
        return <CheckCircle className="h-4 w-4 text-emerald-400" weight="fill" />;
      case 'error':
        return <CheckCircle className="h-4 w-4 text-destructive" weight="fill" />;
      case 'pending':
        return <Clock className="h-4 w-4 text-muted-foreground" weight="fill" />;
      default:
        return null;
    }
  };

  return (
    <motion.div
      initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, x: -20 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
      className={cn('flex gap-3', className)}
    >
      {/* Tool Icon Avatar */}
      <div
        className={cn(
          'flex h-8 w-8 shrink-0 items-center justify-center rounded-full glass-strong border',
          colorClasses
        )}
      >
        <IconComponent className="h-4 w-4" />
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0">
        <div
          className={cn(
            'glass overflow-hidden rounded-xl border transition-shadow duration-200',
            'hover:shadow-lg hover:shadow-accent/5',
            colorClasses.split(' ').slice(1).join(' ')
          )}
        >
          {/* Header */}
          <button
            onClick={onToggle}
            className="flex w-full items-center justify-between px-3 py-2.5 text-left transition-colors hover:bg-white/5"
          >
            <div className="flex items-center gap-2.5">
              <span className={cn('text-sm font-medium', colorClasses.split(' ')[0])}>
                {toolCall.name}
              </span>
              <span className="text-xs text-muted-foreground">Tool Call</span>
              {status !== 'completed' && (
                <div className="flex items-center gap-1">
                  {getStatusIcon()}
                  <span className="text-xs capitalize text-muted-foreground">{status}</span>
                </div>
              )}
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs text-muted-foreground">
                {parameters.length} param{parameters.length !== 1 ? 's' : ''}
              </span>
              <motion.div
                animate={{ rotate: isExpanded ? 180 : 0 }}
                transition={{ duration: shouldReduceMotion ? 0 : 0.2 }}
              >
                <CaretDown className="h-4 w-4 text-muted-foreground" />
              </motion.div>
            </div>
          </button>

          {/* Collapsed Preview */}
          {!isExpanded && parameters.length > 0 && (
            <div className="border-t border-white/5 px-3 py-2">
              <div className="flex flex-wrap gap-2">
                {parameters.slice(0, 3).map((param) => (
                  <span
                    key={param.key}
                    className="inline-flex items-center gap-1 rounded-md bg-white/5 px-2 py-1 text-xs"
                    title={`${param.key}: ${formatValue(param.value)}`}
                  >
                    <span className="text-muted-foreground">{param.key}=</span>
                    <span className="text-foreground/80 truncate max-w-[100px]">
                      {getValuePreview(param.value, 30)}
                    </span>
                  </span>
                ))}
                {parameters.length > 3 && (
                  <span className="text-xs text-muted-foreground">
                    +{parameters.length - 3} more
                  </span>
                )}
              </div>
            </div>
          )}

          {/* Expanded Content */}
          <AnimatePresence initial={false}>
            {isExpanded && (
              <motion.div
                initial={{ height: 0, opacity: 0 }}
                animate={{ height: 'auto', opacity: 1 }}
                exit={{ height: 0, opacity: 0 }}
                transition={{
                  duration: shouldReduceMotion ? 0 : 0.2,
                  ease: [0.16, 1, 0.3, 1],
                }}
                className="overflow-hidden"
              >
                <div className="border-t border-white/5">
                  {/* Copy All Button */}
                  <div className="flex justify-end px-3 pt-2">
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleCopyAll();
                      }}
                      className="flex items-center gap-1 rounded-md px-2 py-1 text-xs text-muted-foreground
                                 transition-colors hover:bg-white/5 hover:text-foreground"
                    >
                      <Copy className="h-3 w-3" />
                      Copy JSON
                    </button>
                  </div>

                  {/* Parameters Grid */}
                  <div className="space-y-2 px-3 py-2">
                    {parameters.map((param) => (
                      <div
                        key={param.key}
                        className="group flex items-start gap-2 rounded-lg bg-black/20 p-2
                                   transition-colors hover:bg-black/30"
                      >
                        <div className="flex-1 min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-medium text-muted-foreground">
                              {param.key}
                            </span>
                            <span className="text-[10px] text-muted-foreground/50 uppercase">
                              {param.type}
                            </span>
                          </div>
                          <div className="mt-0.5 text-sm text-foreground/90 break-all font-mono">
                            {formatValue(param.value)}
                          </div>
                        </div>
                        <button
                          onClick={(e) => {
                            e.stopPropagation();
                            handleCopyParam(param.key, param.value);
                          }}
                          className="opacity-0 group-hover:opacity-100 transition-opacity
                                     rounded-md p-1 hover:bg-white/10"
                          title="Copy value"
                        >
                          {copiedParam === param.key ? (
                            <CheckCircle className="h-3 w-3 text-emerald-400" weight="fill" />
                          ) : (
                            <Copy className="h-3 w-3 text-muted-foreground" />
                          )}
                        </button>
                      </div>
                    ))}
                  </div>

                  {/* Raw JSON View */}
                  <div className="border-t border-white/5 px-3 py-3">
                    <div className="text-xs text-muted-foreground mb-2">Raw JSON</div>
                    <pre
                      className="overflow-x-auto rounded-lg bg-black/30 p-3 text-xs text-foreground/80
                                 scrollbar-thin scrollbar-thumb-accent/20 scrollbar-track-transparent"
                    >
                      <code>{formattedArgs}</code>
                    </pre>
                  </div>
                </div>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </motion.div>
  );
});

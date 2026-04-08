import * as React from 'react';
import { motion, AnimatePresence, useReducedMotion } from 'framer-motion';
import {
  Toolbox,
  CaretDown,
  MagnifyingGlass,
  ArrowsOutLineVertical,
  ArrowsInLineVertical,
  Faders,
  X,
  ChartBar,
  CheckCircle,
  XCircle,
  Clock,
} from '@phosphor-icons/react';
import { ToolCall } from './ToolCall';
import { ToolResult } from './ToolResult';
import { useTools } from '@/hooks/useTools';
import type { ToolCall as ToolCallType, ToolResult as ToolResultType } from '@/types';
import { cn } from '@/lib/utils';

interface ToolPanelProps {
  toolCalls?: ToolCallType[];
  toolResults?: ToolResultType[];
  className?: string;
}

type GroupBy = 'none' | 'type' | 'status';
type SortBy = 'time' | 'name' | 'status';

interface ToolItem {
  id: string;
  type: 'call' | 'result';
  name: string;
  status: 'pending' | 'executing' | 'completed' | 'error';
  data: ToolCallType | ToolResultType;
  timestamp?: number;
}

// Tool categories for grouping
const toolCategories: Record<string, string> = {
  read_file: 'File Operations',
  write_file: 'File Operations',
  edit_file: 'File Operations',
  glob: 'File Operations',
  bash: 'System Commands',
  command: 'System Commands',
  exec: 'System Commands',
  grep: 'Search',
  search: 'Search',
  find: 'Search',
  web_search: 'Web',
  web_fetch: 'Web',
  fetch: 'Web',
  http: 'Web',
  db_query: 'Database',
  database: 'Database',
  sql: 'Database',
};

export function ToolPanel({ toolCalls, toolResults, className }: ToolPanelProps) {
  const [isExpanded, setIsExpanded] = React.useState(true);
  const [searchQuery, setSearchQuery] = React.useState('');
  const [groupBy, setGroupBy] = React.useState<GroupBy>('none');
  const [sortBy, setSortBy] = React.useState<SortBy>('time');
  const [showFilters, setShowFilters] = React.useState(false);
  const { toggleCall, toggleResult, isCallExpanded, isResultExpanded, collapseAll, expandAll } =
    useTools();
  const shouldReduceMotion = useReducedMotion();

  // Combine and normalize tool items
  const toolItems: ToolItem[] = React.useMemo(() => {
    const items: ToolItem[] = [];

    toolCalls?.forEach((call, index) => {
      items.push({
        id: call.id,
        type: 'call',
        name: call.name,
        status: 'executing',
        data: call,
        timestamp: Date.now() - (toolCalls.length - index) * 1000,
      });
    });

    toolResults?.forEach((result, index) => {
      items.push({
        id: result.id || `${result.name}-${index}`,
        type: 'result',
        name: result.name,
        status: result.success ? 'completed' : 'error',
        data: result,
        timestamp: Date.now() - (toolResults.length - index) * 1000,
      });
    });

    return items;
  }, [toolCalls, toolResults]);

  // Filter items based on search
  const filteredItems = React.useMemo(() => {
    if (!searchQuery.trim()) return toolItems;

    const query = searchQuery.toLowerCase();
    return toolItems.filter(
      (item) =>
        item.name.toLowerCase().includes(query) ||
        JSON.stringify(item.data).toLowerCase().includes(query)
    );
  }, [toolItems, searchQuery]);

  // Sort items
  const sortedItems = React.useMemo(() => {
    const items = [...filteredItems];
    switch (sortBy) {
      case 'name':
        return items.sort((a, b) => a.name.localeCompare(b.name));
      case 'status':
        return items.sort((a, b) => a.status.localeCompare(b.status));
      case 'time':
      default:
        return items;
    }
  }, [filteredItems, sortBy]);

  // Group items
  const groupedItems = React.useMemo(() => {
    if (groupBy === 'none') return { All: sortedItems };

    const groups: Record<string, ToolItem[]> = {};

    sortedItems.forEach((item) => {
      let groupKey: string;

      if (groupBy === 'type') {
        groupKey = toolCategories[item.name] || 'Other';
      } else if (groupBy === 'status') {
        groupKey =
          item.status === 'completed'
            ? 'Completed'
            : item.status === 'error'
              ? 'Failed'
              : item.status === 'executing'
                ? 'Executing'
                : 'Pending';
      } else {
        groupKey = 'All';
      }

      if (!groups[groupKey]) groups[groupKey] = [];
      groups[groupKey].push(item);
    });

    return groups;
  }, [sortedItems, groupBy]);

  // Statistics
  const stats = React.useMemo(() => {
    const total = toolItems.length;
    const completed = toolItems.filter((i) => i.status === 'completed').length;
    const errors = toolItems.filter((i) => i.status === 'error').length;
    const executing = toolItems.filter((i) => i.status === 'executing').length;
    const uniqueTools = new Set(toolItems.map((i) => i.name)).size;

    return { total, completed, errors, executing, uniqueTools };
  }, [toolItems]);

  const hasTools = toolItems.length > 0;

  if (!hasTools) return null;

  const handleExpandAll = () => {
    toolCalls?.forEach((call) => {
      if (!isCallExpanded(call.id)) toggleCall(call.id);
    });
    toolResults?.forEach((result) => {
      if (!isResultExpanded(result.name)) toggleResult(result.name);
    });
  };

  const handleCollapseAll = () => {
    collapseAll();
  };

  return (
    <motion.div
      initial={shouldReduceMotion ? { opacity: 1 } : { opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
      className={cn('mb-4 overflow-hidden rounded-xl glass border border-accent/20', className)}
    >
      {/* Header */}
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="flex w-full items-center justify-between px-4 py-3 text-left transition-colors hover:bg-white/5"
      >
        <div className="flex items-center gap-2">
          <div className="flex h-6 w-6 items-center justify-center rounded-lg bg-accent/20">
            <Toolbox className="h-3.5 w-3.5 text-accent" weight="duotone" />
          </div>
          <span className="text-sm font-medium text-foreground">Tool Calls</span>
          <span
            className="flex h-5 min-w-[1.25rem] items-center justify-center rounded-full
                         bg-accent/20 px-1.5 text-xs font-medium text-accent"
          >
            {stats.total}
          </span>
          {stats.errors > 0 && (
            <span
              className="flex h-5 min-w-[1.25rem] items-center justify-center rounded-full
                           bg-destructive/20 px-1.5 text-xs font-medium text-destructive"
            >
              {stats.errors} failed
            </span>
          )}
        </div>
        <motion.div
          animate={{ rotate: isExpanded ? 180 : 0 }}
          transition={{ duration: shouldReduceMotion ? 0 : 0.2 }}
        >
          <CaretDown className="h-4 w-4 text-muted-foreground" />
        </motion.div>
      </button>

      {/* Content */}
      <AnimatePresence initial={false}>
        {isExpanded && (
          <motion.div
            initial={{ height: 0 }}
            animate={{ height: 'auto' }}
            exit={{ height: 0 }}
            transition={{ duration: shouldReduceMotion ? 0 : 0.3, ease: [0.16, 1, 0.3, 1] }}
            className="overflow-hidden"
          >
            <div className="border-t border-white/5">
              {/* Search and Filters Bar */}
              <div className="flex items-center gap-2 p-3 border-b border-white/5">
                <div className="relative flex-1">
                  <MagnifyingGlass className="absolute left-2.5 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground" />
                  <input
                    type="text"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    placeholder="Search tools..."
                    className="w-full rounded-lg bg-black/20 pl-9 pr-8 py-1.5 text-sm
                               placeholder:text-muted-foreground/50 text-foreground
                               border border-white/5 focus:border-accent/30 focus:outline-none
                               focus:ring-1 focus:ring-accent/20 transition-colors"
                  />
                  {searchQuery && (
                    <button
                      onClick={() => setSearchQuery('')}
                      className="absolute right-2 top-1/2 -translate-y-1/2 p-0.5
                                 text-muted-foreground hover:text-foreground transition-colors"
                    >
                      <X className="h-3 w-3" />
                    </button>
                  )}
                </div>
                <button
                  onClick={() => setShowFilters(!showFilters)}
                  className={cn(
                    'flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors',
                    showFilters
                      ? 'bg-accent/20 text-accent'
                      : 'bg-black/20 text-muted-foreground hover:text-foreground'
                  )}
                >
                  <Faders className="h-3.5 w-3.5" />
                  Filters
                </button>
              </div>

              {/* Filters Panel */}
              <AnimatePresence>
                {showFilters && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: 'auto', opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    transition={{ duration: shouldReduceMotion ? 0 : 0.2 }}
                    className="overflow-hidden border-b border-white/5"
                  >
                    <div className="p-3 space-y-3">
                      {/* Group By */}
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-muted-foreground">Group by</span>
                        <div className="flex items-center gap-1">
                          {(['none', 'type', 'status'] as GroupBy[]).map((option) => (
                            <button
                              key={option}
                              onClick={() => setGroupBy(option)}
                              className={cn(
                                'px-2.5 py-1 rounded-md text-xs capitalize transition-colors',
                                groupBy === option
                                  ? 'bg-white/10 text-foreground'
                                  : 'text-muted-foreground hover:text-foreground hover:bg-white/5'
                              )}
                            >
                              {option}
                            </button>
                          ))}
                        </div>
                      </div>

                      {/* Sort By */}
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-muted-foreground">Sort by</span>
                        <div className="flex items-center gap-1">
                          {(['time', 'name', 'status'] as SortBy[]).map((option) => (
                            <button
                              key={option}
                              onClick={() => setSortBy(option)}
                              className={cn(
                                'px-2.5 py-1 rounded-md text-xs capitalize transition-colors',
                                sortBy === option
                                  ? 'bg-white/10 text-foreground'
                                  : 'text-muted-foreground hover:text-foreground hover:bg-white/5'
                              )}
                            >
                              {option}
                            </button>
                          ))}
                        </div>
                      </div>

                      {/* Quick Actions */}
                      <div className="flex items-center gap-2 pt-2 border-t border-white/5">
                        <button
                          onClick={handleExpandAll}
                          className="flex items-center gap-1.5 text-xs text-muted-foreground
                                     hover:text-foreground transition-colors"
                        >
                          <ArrowsOutLineVertical className="h-3.5 w-3.5" />
                          Expand All
                        </button>
                        <button
                          onClick={handleCollapseAll}
                          className="flex items-center gap-1.5 text-xs text-muted-foreground
                                     hover:text-foreground transition-colors"
                        >
                          <ArrowsInLineVertical className="h-3.5 w-3.5" />
                          Collapse All
                        </button>
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>

              {/* Statistics Bar */}
              <div
                className="flex items-center gap-4 px-3 py-2 border-b border-white/5
                              bg-white/[0.02]"
              >
                <div className="flex items-center gap-1.5">
                  <ChartBar className="h-3.5 w-3.5 text-muted-foreground" />
                  <span className="text-xs text-muted-foreground">Stats:</span>
                </div>
                <div className="flex items-center gap-3">
                  <div className="flex items-center gap-1">
                    <CheckCircle className="h-3 w-3 text-emerald-400" />
                    <span className="text-xs text-emerald-400">{stats.completed}</span>
                  </div>
                  {stats.errors > 0 && (
                    <div className="flex items-center gap-1">
                      <XCircle className="h-3 w-3 text-destructive" />
                      <span className="text-xs text-destructive">{stats.errors}</span>
                    </div>
                  )}
                  {stats.executing > 0 && (
                    <div className="flex items-center gap-1">
                      <Clock className="h-3 w-3 text-amber-400" />
                      <span className="text-xs text-amber-400">{stats.executing}</span>
                    </div>
                  )}
                  <span className="text-xs text-muted-foreground">
                    {stats.uniqueTools} unique tools
                  </span>
                </div>
              </div>

              {/* Tool List */}
              <div className="p-4 space-y-4 max-h-[600px] overflow-y-auto scrollbar-thin scrollbar-thumb-accent/20">
                {Object.entries(groupedItems).map(([groupName, items]) => (
                  <div key={groupName}>
                    {groupBy !== 'none' && items.length > 0 && (
                      <div className="sticky top-0 z-10 mb-2 flex items-center gap-2">
                        <span className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
                          {groupName}
                        </span>
                        <span className="text-xs text-muted-foreground/50">({items.length})</span>
                        <div className="flex-1 h-px bg-white/5" />
                      </div>
                    )}
                    <div className="space-y-3">
                      {items.map((item) =>
                        item.type === 'call' ? (
                          <ToolCall
                            key={item.id}
                            toolCall={item.data as ToolCallType}
                            isExpanded={isCallExpanded(item.id)}
                            onToggle={() => toggleCall(item.id)}
                            status={item.status}
                          />
                        ) : (
                          <ToolResult
                            key={item.id}
                            toolResult={item.data as ToolResultType}
                            isExpanded={isResultExpanded(item.name)}
                            onToggle={() => toggleResult(item.name)}
                          />
                        )
                      )}
                    </div>
                  </div>
                ))}

                {filteredItems.length === 0 && searchQuery && (
                  <div className="text-center py-8">
                    <p className="text-sm text-muted-foreground">
                      No tools match &quot;{searchQuery}&quot;
                    </p>
                    <button
                      onClick={() => setSearchQuery('')}
                      className="mt-2 text-xs text-accent hover:underline"
                    >
                      Clear search
                    </button>
                  </div>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
}

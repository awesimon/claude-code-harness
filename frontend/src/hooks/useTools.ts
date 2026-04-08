import { useState, useCallback, useMemo } from 'react';
import type { ToolCall, ToolResult } from '@/types';

interface ToolState {
  expandedCalls: Set<string>;
  expandedResults: Set<string>;
}

export function useTools() {
  const [state, setState] = useState<ToolState>({
    expandedCalls: new Set(),
    expandedResults: new Set(),
  });

  const toggleCall = useCallback((id: string) => {
    setState((prev) => {
      const newExpanded = new Set(prev.expandedCalls);
      if (newExpanded.has(id)) {
        newExpanded.delete(id);
      } else {
        newExpanded.add(id);
      }
      return { ...prev, expandedCalls: newExpanded };
    });
  }, []);

  const toggleResult = useCallback((id: string) => {
    setState((prev) => {
      const newExpanded = new Set(prev.expandedResults);
      if (newExpanded.has(id)) {
        newExpanded.delete(id);
      } else {
        newExpanded.add(id);
      }
      return { ...prev, expandedResults: newExpanded };
    });
  }, []);

  const expandAll = useCallback((callIds?: string[], resultIds?: string[]) => {
    setState({
      expandedCalls: new Set(callIds || []),
      expandedResults: new Set(resultIds || []),
    });
  }, []);

  const collapseAll = useCallback(() => {
    setState({
      expandedCalls: new Set(),
      expandedResults: new Set(),
    });
  }, []);

  const expandCall = useCallback((id: string) => {
    setState((prev) => {
      const newExpanded = new Set(prev.expandedCalls);
      newExpanded.add(id);
      return { ...prev, expandedCalls: newExpanded };
    });
  }, []);

  const expandResult = useCallback((id: string) => {
    setState((prev) => {
      const newExpanded = new Set(prev.expandedResults);
      newExpanded.add(id);
      return { ...prev, expandedResults: newExpanded };
    });
  }, []);

  const collapseCall = useCallback((id: string) => {
    setState((prev) => {
      const newExpanded = new Set(prev.expandedCalls);
      newExpanded.delete(id);
      return { ...prev, expandedCalls: newExpanded };
    });
  }, []);

  const collapseResult = useCallback((id: string) => {
    setState((prev) => {
      const newExpanded = new Set(prev.expandedResults);
      newExpanded.delete(id);
      return { ...prev, expandedResults: newExpanded };
    });
  }, []);

  const isCallExpanded = useCallback(
    (id: string) => state.expandedCalls.has(id),
    [state.expandedCalls]
  );

  const isResultExpanded = useCallback(
    (id: string) => state.expandedResults.has(id),
    [state.expandedResults]
  );

  // Stats
  const stats = useMemo(
    () => ({
      expandedCallsCount: state.expandedCalls.size,
      expandedResultsCount: state.expandedResults.size,
    }),
    [state.expandedCalls, state.expandedResults]
  );

  return {
    // State
    expandedCalls: state.expandedCalls,
    expandedResults: state.expandedResults,
    // Toggles
    toggleCall,
    toggleResult,
    // Bulk operations
    expandAll,
    collapseAll,
    expandCall,
    expandResult,
    collapseCall,
    collapseResult,
    // Queries
    isCallExpanded,
    isResultExpanded,
    // Stats
    stats,
  };
}

// Hook for managing a single tool call/result pair
export function useToolPair(
  toolCall: ToolCall | undefined,
  toolResult: ToolResult | undefined
) {
  const [isExpanded, setIsExpanded] = useState(false);

  const toggle = useCallback(() => {
    setIsExpanded((prev) => !prev);
  }, []);

  const expand = useCallback(() => {
    setIsExpanded(true);
  }, []);

  const collapse = useCallback(() => {
    setIsExpanded(false);
  }, []);

  const status = useMemo(() => {
    if (!toolCall) return 'idle';
    if (!toolResult) return 'executing';
    return toolResult.success ? 'completed' : 'error';
  }, [toolCall, toolResult]);

  return {
    isExpanded,
    toggle,
    expand,
    collapse,
    status,
  };
}

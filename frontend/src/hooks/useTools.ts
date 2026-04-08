import { useState, useCallback } from 'react';
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

  const expandAll = useCallback(() => {
    // This would need to be called with the list of tool IDs
    // For now, we'll handle this in the component
  }, []);

  const collapseAll = useCallback(() => {
    setState({
      expandedCalls: new Set(),
      expandedResults: new Set(),
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

  return {
    toggleCall,
    toggleResult,
    expandAll,
    collapseAll,
    isCallExpanded,
    isResultExpanded,
  };
}

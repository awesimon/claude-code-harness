import type {
  Conversation,
  Message,
  Task,
  CreateConversationResponse,
  SendMessageRequest,
  SSEEvent,
} from '@/types';

const API_BASE = '/api/v1';
const LEGACY_API_BASE = '';

export class APIError extends Error {
  constructor(
    message: string,
    public status?: number,
    public code?: string
  ) {
    super(message);
    this.name = 'APIError';
  }
}

// ==================== Conversation APIs ====================

export async function createConversation(title?: string): Promise<Conversation> {
  const response = await fetch(`${API_BASE}/conversations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  });

  if (!response.ok) {
    throw new APIError(`Failed to create conversation: ${response.statusText}`, response.status);
  }

  return response.json();
}

export async function listConversations(limit: number = 50): Promise<Conversation[]> {
  const response = await fetch(`${API_BASE}/conversations?limit=${limit}`);

  if (!response.ok) {
    throw new APIError(`Failed to list conversations: ${response.statusText}`, response.status);
  }

  return response.json();
}

export async function getConversation(conversationId: string): Promise<Conversation & { messages: Message[]; tasks: Task[] }> {
  const response = await fetch(`${API_BASE}/conversations/${conversationId}`);

  if (!response.ok) {
    throw new APIError(`Failed to get conversation: ${response.statusText}`, response.status);
  }

  return response.json();
}

export async function updateConversation(conversationId: string, title: string): Promise<Conversation> {
  const response = await fetch(`${API_BASE}/conversations/${conversationId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title }),
  });

  if (!response.ok) {
    throw new APIError(`Failed to update conversation: ${response.statusText}`, response.status);
  }

  return response.json();
}

export async function deleteConversation(conversationId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/conversations/${conversationId}`, {
    method: 'DELETE',
  });

  if (!response.ok) {
    throw new APIError(`Failed to delete conversation: ${response.statusText}`, response.status);
  }
}

// ==================== Message APIs ====================

export async function addMessage(
  conversationId: string,
  role: string,
  content: string,
  toolCalls?: any[],
  toolResults?: any[]
): Promise<Message> {
  const response = await fetch(`${API_BASE}/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ role, content, tool_calls: toolCalls, tool_results: toolResults }),
  });

  if (!response.ok) {
    throw new APIError(`Failed to add message: ${response.statusText}`, response.status);
  }

  return response.json();
}

export async function getMessages(conversationId: string, limit: number = 100): Promise<Message[]> {
  const response = await fetch(`${API_BASE}/conversations/${conversationId}/messages?limit=${limit}`);

  if (!response.ok) {
    throw new APIError(`Failed to get messages: ${response.statusText}`, response.status);
  }

  return response.json();
}

// ==================== Task APIs ====================

export async function createTask(task: {
  subject: string;
  description: string;
  conversation_id?: string;
  active_form?: string;
  owner?: string;
  status?: 'pending' | 'in_progress' | 'completed';
  blocks?: string[];
  blocked_by?: string[];
  meta?: Record<string, any>;
}): Promise<Task> {
  const response = await fetch(`${API_BASE}/tasks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(task),
  });

  if (!response.ok) {
    throw new APIError(`Failed to create task: ${response.statusText}`, response.status);
  }

  return response.json();
}

export async function listTasks(filters?: {
  conversation_id?: string;
  status?: string;
  owner?: string;
}): Promise<Task[]> {
  const params = new URLSearchParams();
  if (filters?.conversation_id) params.append('conversation_id', filters.conversation_id);
  if (filters?.status) params.append('status', filters.status);
  if (filters?.owner) params.append('owner', filters.owner);

  const response = await fetch(`${API_BASE}/tasks?${params.toString()}`);

  if (!response.ok) {
    throw new APIError(`Failed to list tasks: ${response.statusText}`, response.status);
  }

  return response.json();
}

export async function getTask(taskId: string): Promise<Task> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}`);

  if (!response.ok) {
    throw new APIError(`Failed to get task: ${response.statusText}`, response.status);
  }

  return response.json();
}

export async function updateTask(
  taskId: string,
  updates: Partial<Task>
): Promise<Task> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(updates),
  });

  if (!response.ok) {
    throw new APIError(`Failed to update task: ${response.statusText}`, response.status);
  }

  return response.json();
}

export async function deleteTask(taskId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}`, {
    method: 'DELETE',
  });

  if (!response.ok) {
    throw new APIError(`Failed to delete task: ${response.statusText}`, response.status);
  }
}

export async function claimTask(
  taskId: string,
  agentId: string,
  checkAgentBusy: boolean = false
): Promise<{
  success: boolean;
  reason?: string;
  task?: Task;
  busy_with_tasks?: string[];
  blocked_by_tasks?: string[];
}> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}/claim`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent_id: agentId, check_agent_busy: checkAgentBusy }),
  });

  if (!response.ok) {
    throw new APIError(`Failed to claim task: ${response.statusText}`, response.status);
  }

  return response.json();
}

export async function unassignTask(taskId: string): Promise<Task> {
  const response = await fetch(`${API_BASE}/tasks/${taskId}/unassign`, {
    method: 'POST',
  });

  if (!response.ok) {
    throw new APIError(`Failed to unassign task: ${response.statusText}`, response.status);
  }

  const data = await response.json();
  return data.task;
}

export async function blockTask(fromTaskId: string, toTaskId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/tasks/${fromTaskId}/block/${toTaskId}`, {
    method: 'POST',
  });

  if (!response.ok) {
    throw new APIError(`Failed to block task: ${response.statusText}`, response.status);
  }
}

// ==================== Plan APIs ====================

export async function createOrUpdatePlan(
  conversationId: string,
  content: string
): Promise<{ id: string; conversation_id: string; content: string }> {
  const response = await fetch(`${API_BASE}/plans`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ conversation_id: conversationId, content }),
  });

  if (!response.ok) {
    throw new APIError(`Failed to create/update plan: ${response.statusText}`, response.status);
  }

  return response.json();
}

export async function getPlanByConversation(conversationId: string): Promise<{ id: string; conversation_id: string; content: string } | null> {
  const response = await fetch(`${API_BASE}/plans/conversation/${conversationId}`);

  if (response.status === 404) {
    return null;
  }

  if (!response.ok) {
    throw new APIError(`Failed to get plan: ${response.statusText}`, response.status);
  }

  return response.json();
}

// ==================== Legacy Chat APIs ====================

export async function createLegacyConversation(): Promise<string> {
  const response = await fetch(`${LEGACY_API_BASE}/chat/create`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });

  if (!response.ok) {
    throw new APIError(`Failed to create conversation: ${response.statusText}`, response.status);
  }

  const data: CreateConversationResponse = await response.json();
  if (!data.success) {
    throw new APIError('Failed to create conversation');
  }

  return data.data.conversation_id;
}

export async function deleteLegacyConversation(conversationId: string): Promise<void> {
  const response = await fetch(`${LEGACY_API_BASE}/chat/${conversationId}`, {
    method: 'DELETE',
  });

  if (!response.ok) {
    throw new APIError(`Failed to delete conversation: ${response.statusText}`, response.status);
  }
}

export interface StreamCallbacks {
  onEvent: (event: SSEEvent) => void;
  onError: (error: Error) => void;
  onComplete: () => void;
}

export async function streamMessage(
  request: SendMessageRequest,
  callbacks: StreamCallbacks,
  abortSignal?: AbortSignal
): Promise<void> {
  try {
    const response = await fetch(`${LEGACY_API_BASE}/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(request),
      signal: abortSignal,
    });

    if (!response.ok) {
      throw new APIError(`Stream request failed: ${response.statusText}`, response.status);
    }

    if (!response.body) {
      throw new APIError('No response body');
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    const processPayload = (raw: string): boolean => {
      const data = raw.trim();
      if (data === '[DONE]') {
        callbacks.onComplete();
        return true;
      }
      if (!data) return false;
      try {
        const event: SSEEvent = JSON.parse(data);
        callbacks.onEvent(event);
      } catch (e) {
        console.error('Failed to parse SSE event:', e);
      }
      return false;
    };

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (value) {
          buffer += decoder.decode(value, { stream: true });
        }
        buffer = buffer.replace(/\r\n/g, '\n').replace(/\r/g, '\n');
        const lines = buffer.split('\n');
        buffer = done ? '' : (lines.pop() ?? '');

        for (const line of lines) {
          if (line.startsWith('data:')) {
            const payload = line.startsWith('data: ') ? line.slice(6) : line.slice(5);
            if (processPayload(payload)) {
              return;
            }
          }
        }

        if (done) break;
      }

      const tail = buffer.trim();
      if (tail.startsWith('data:')) {
        const payload = tail.startsWith('data: ') ? tail.slice(6) : tail.slice(5);
        if (processPayload(payload)) {
          return;
        }
      }

      callbacks.onComplete();
    } catch (error) {
      if (error instanceof Error && error.name === 'AbortError') {
        callbacks.onComplete();
        return;
      }
      throw error;
    } finally {
      reader.releaseLock();
    }
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') {
      callbacks.onComplete();
      return;
    }
    callbacks.onError(error instanceof Error ? error : new Error(String(error)));
  }
}

export async function checkHealth(): Promise<boolean> {
  try {
    const response = await fetch(`${LEGACY_API_BASE}/health`, {
      method: 'GET',
      signal: AbortSignal.timeout(5000),
    });
    return response.ok;
  } catch {
    return false;
  }
}

// ==================== WebSocket ====================

export function createWebSocket(conversationId?: string): WebSocket {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const host = window.location.host;
  const path = conversationId
    ? `${API_BASE}/ws/conversations/${conversationId}`
    : `${API_BASE}/ws`;

  return new WebSocket(`${protocol}//${host}${path}`);
}

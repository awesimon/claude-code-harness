import type {
  CreateConversationResponse,
  SendMessageRequest,
  SSEEvent,
} from '@/types';

const API_BASE = '';

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

export async function createConversation(): Promise<string> {
  const response = await fetch(`${API_BASE}/chat/create`, {
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

export async function deleteConversation(conversationId: string): Promise<void> {
  const response = await fetch(`${API_BASE}/chat/${conversationId}`, {
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
    const response = await fetch(`${API_BASE}/chat/stream`, {
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

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6);

            if (data === '[DONE]') {
              callbacks.onComplete();
              return;
            }

            try {
              const event: SSEEvent = JSON.parse(data);
              callbacks.onEvent(event);
            } catch (e) {
              console.error('Failed to parse SSE event:', e);
            }
          }
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
    const response = await fetch(`${API_BASE}/health`, {
      method: 'GET',
      signal: AbortSignal.timeout(5000),
    });
    return response.ok;
  } catch {
    return false;
  }
}

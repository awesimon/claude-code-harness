// Chat types
export interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system' | 'tool';
  content: string;
  timestamp: number;
  toolCalls?: ToolCall[];
  toolResults?: ToolResult[];
  thinking?: string; // 思考过程
  thinkingTime?: number; // 思考耗时(ms)
}

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface ToolResult {
  id: string;
  name: string;
  success: boolean;
  result: unknown;
  error?: string;
}

export interface Conversation {
  id: string;
  title: string | null;
  messages?: Message[];
  createdAt: number;
  updatedAt: number;
}

export type ConnectionStatus = 'connected' | 'disconnected' | 'connecting' | 'error';

// Model type is now dynamic - any string is valid
export type ModelType = string;

export interface ChatState {
  conversations: Conversation[];
  currentConversationId: string | null;
  messages: Message[];
  isProcessing: boolean;
  status: 'idle' | 'thinking' | 'tool_calling' | 'streaming';
  connectionStatus: ConnectionStatus;
  selectedModel: ModelType;
  error: string | null;
}

export interface ChatActions {
  setCurrentConversation: (id: string | null) => void;
  addMessage: (message: Omit<Message, 'id' | 'timestamp'>) => string;
  updateMessage: (id: string, content: string) => void;
  updateMessageToolCalls: (id: string, toolCalls: ToolCall[]) => void;
  updateMessageToolResults: (id: string, toolResults: ToolResult[]) => void;
  updateMessageThinking: (id: string, thinking: string) => void;
  setProcessing: (isProcessing: boolean) => void;
  setStatus: (status: ChatState['status']) => void;
  setConnectionStatus: (status: ConnectionStatus) => void;
  setSelectedModel: (model: ModelType) => void;
  setError: (error: string | null) => void;
  clearMessages: () => void;
  addConversation: (conversation: Conversation) => void;
  removeConversation: (id: string) => void;
  loadConversation: (id: string) => void;
}

// Task types
export interface Task {
  id: string;
  subject: string;
  description: string;
  active_form?: string;
  owner?: string;
  status: 'pending' | 'in_progress' | 'completed';
  blocks?: string[];
  blocked_by?: string[];
  meta?: Record<string, any>;
  created_at?: string;
  updated_at?: string;
  conversation_id?: string;
}

// SSE Event types
export interface SSEEvent {
  type: 'user_message' | 'assistant_message' | 'thinking' | 'tool_call' | 'tool_result' | 'state_change' | 'error' | 'done';
  data?: unknown;
  content?: string;
  tool_calls?: ToolCall[];
  tool_call_id?: string;
  name?: string;
  success?: boolean;
  result?: unknown;
  error?: string;
  state?: string;
  is_streaming?: boolean;
}

export interface UserMessageEvent extends SSEEvent {
  type: 'user_message';
  content: string;
}

export interface AssistantMessageEvent extends SSEEvent {
  type: 'assistant_message';
  content: string;
}

export interface ToolCallEvent extends SSEEvent {
  type: 'tool_call';
  tool_calls: ToolCall[];
  state?: string;
}

export interface ToolResultEvent extends SSEEvent {
  type: 'tool_result';
  tool_call_id?: string;
  name: string;
  success: boolean;
  result: unknown;
  error?: string;
}

export interface StateChangeEvent extends SSEEvent {
  type: 'state_change';
  state: 'thinking' | 'tool_calling' | 'idle';
}

export interface ErrorEvent extends SSEEvent {
  type: 'error';
  error: string;
}

export interface DoneEvent extends SSEEvent {
  type: 'done';
}

// API types
export interface CreateConversationResponse {
  success: boolean;
  data: {
    conversation_id: string;
  };
}

export interface SendMessageRequest {
  message: string;
  conversation_id: string;
}

// Component props
export interface MessageProps {
  message: Message;
  isLast?: boolean;
}

export interface ToolCallProps {
  toolCall: ToolCall;
  isExpanded?: boolean;
  onToggle?: () => void;
}

export interface ToolResultProps {
  toolResult: ToolResult;
  isExpanded?: boolean;
  onToggle?: () => void;
}

export interface SidebarProps {
  onNewChat: () => void;
  onSelectConversation: (id: string) => void;
  onDeleteConversation?: (id: string) => void;
}

export interface ChatInputProps {
  onSend: (message: string) => void;
  disabled?: boolean;
  placeholder?: string;
}

export interface StatusIndicatorProps {
  status: ChatState['status'];
}

export interface ConnectionIndicatorProps {
  status: ConnectionStatus;
}

# Claude Code Frontend

Engineered React + TypeScript frontend for Claude Code Python API.

## Tech Stack

- **Framework**: React 18 + TypeScript
- **Build Tool**: Vite
- **Styling**: Tailwind CSS
- **State Management**: Zustand (with persistence)
- **Animation**: Framer Motion
- **Icons**: Phosphor Icons
- **Markdown**: marked + highlight.js
- **UI Components**: Radix UI primitives

## Project Structure

```
frontend/
├── src/
│   ├── components/
│   │   ├── ui/           # Base components (Button, Input, Card, Select, Alert)
│   │   ├── chat/         # Chat components (Message, MessageList, ChatInput, Sidebar)
│   │   └── tools/        # Tool display (ToolCall, ToolResult, ToolPanel)
│   ├── hooks/
│   │   ├── useChat.ts    # Chat logic & message handling
│   │   ├── useSSE.ts     # SSE connection & health check
│   │   └── useTools.ts   # Tool call/result expand/collapse state
│   ├── stores/
│   │   └── chatStore.ts  # Zustand state management
│   ├── types/
│   │   └── index.ts      # TypeScript type definitions
│   ├── lib/
│   │   ├── api.ts        # API client & SSE stream handler
│   │   └── utils.ts      # Utility functions
│   ├── App.tsx           # Main application component
│   ├── main.tsx          # Entry point
│   └── index.css         # Global styles
├── index.html
├── package.json
├── tsconfig.json
├── vite.config.ts
└── tailwind.config.js
```

## Features

### Core Features (from original chat.html)
- Create/switch/clear conversations
- SSE streaming response handling
- Tool call display (tool_call, tool_result)
- Status display (thinking, tool_calling)
- Code highlighting (highlight.js)
- Markdown rendering (marked)

### Enhanced Features
- Message enter animation (fadeIn + slideUp with Framer Motion)
- Tool call expand/collapse
- Connection status indicator with auto-reconnect
- Error retry mechanism (3 attempts with exponential backoff)
- Mobile responsive design
- Type-safe state management with Zustand
- Optimized performance (React.memo, useMemo, code splitting)
- Accessibility support (aria-label, keyboard navigation)

## Quick Start

```bash
# Install dependencies
npm install

# Start dev server (with API proxy to localhost:8000)
npm run dev

# Build for production
npm run build

# Type check
npm run typecheck

# Lint
npm run lint
```

## API Integration

The frontend expects the following API endpoints:

- `POST /chat/create` - Create new conversation
- `POST /chat/stream` - Stream messages (SSE)
- `DELETE /chat/:id` - Delete conversation
- `GET /health` - Health check

Configure proxy in `vite.config.ts`:
```typescript
server: {
  proxy: {
    '/chat': 'http://localhost:8000',
    '/health': 'http://localhost:8000',
  }
}
```

## Component Design

### UI Components
All base components follow shadcn/ui patterns with custom dark theme styling:
- **Button**: Variants (default, secondary, outline, ghost, destructive)
- **Input/Textarea**: Auto-resizing textarea for chat input
- **Card**: Container with consistent border/shadow
- **Select**: Model selection dropdown
- **Alert**: Error display with dismiss

### Chat Components
- **Message**: Renders markdown with syntax highlighting
- **MessageList**: Virtual scrolling with auto-scroll to bottom
- **ChatInput**: Auto-resizing textarea with send/stop buttons
- **Sidebar**: Conversation list with collapsible state
- **StatusIndicator**: Thinking/tool_calling status badge
- **QuickActions**: Suggested command buttons

### Tool Components
- **ToolCall**: Expandable tool call display
- **ToolResult**: Success/error result with truncation
- **ToolPanel**: Container for all tool calls in a message

## State Management

Zustand store provides:
- Conversation persistence to localStorage
- Current conversation tracking
- Message history
- Processing status
- Connection status
- Selected model preference

## Performance Optimizations

1. **React.memo** on Message, ToolCall, ToolResult components
2. **useMemo** for markdown parsing and JSON formatting
3. **Code splitting** via dynamic imports (ready for lazy loading)
4. **Manual chunks** in Vite config for vendor libraries
5. **AbortController** for canceling in-flight requests

## Accessibility

- Keyboard navigation (Tab, Enter, Shift+Enter)
- ARIA labels on interactive elements
- Focus visible states
- Reduced motion support (via Framer Motion's built-in checks)

## License

MIT

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### Added

#### Core Features
- **Context Compaction Service** - Implemented automatic conversation context compression to handle long conversations without hitting token limits
  - Token counter with support for mixed Chinese/English content and multimodal inputs
  - Compaction strategies (full compaction, partial compaction, summary generation)
  - Auto-compaction with threshold detection and circuit breaker protection
  - Reactive compaction for handling API errors (413 Payload Too Large)
  - Boundary markers for tracking compaction points in conversation history

- **Error Recovery System** - Comprehensive error handling and recovery mechanisms
  - Error classification system (recoverable vs non-recoverable errors)
  - Max output tokens recovery with progressive token limit increases (1.5x/2x/4x/8x)
  - Prompt too long error handling with truncation and compression strategies
  - Exponential backoff retry mechanism with jitter
  - Circuit breaker pattern to prevent cascade failures
  - Recovery manager for coordinating multiple recovery strategies

#### Frontend Improvements
- **Performance Optimizations**
  - Added message content length limiting (50KB per message, 100KB max accumulation)
  - Limited message list to 100 most recent messages to prevent DOM overflow
  - Limited visible messages to 50 for rendering performance
  - Removed layout animations causing frequent recalculations
  - Added error handling for markdown parsing failures
  - Optimized React.memo comparison for message components

- **UI/UX Enhancements**
  - Fixed message ordering - tool calls now display after assistant content
  - Added truncation indicators for long messages
  - Added message count indicator when messages are hidden
  - Improved streaming message handling with proper state tracking

#### API & Backend
- **Query Engine Enhancements**
  - Integrated error recovery manager into chat and chat_stream methods
  - Added support for error categorization in API responses
  - Improved token budget tracking across conversation turns

- **LLM Service Improvements**
  - Conditional tool parameter handling for providers that don't support tools
  - Better error logging for API failures
  - Provider capability detection

### Changed
- Updated message rendering order: Content → Tool Calls → Tool Results
- Optimized animation delays in message list (reduced from 0.05s to 0.02s per message)
- Changed max visible messages from unlimited to 50 for performance

### Fixed
- Fixed frontend freezing when processing long conversations
- Fixed text compression/deformation in message display
- Fixed tool calls appearing before assistant message content
- Fixed memory leak from unlimited message accumulation
- Fixed React re-rendering issues with streaming content

## [0.4.0] - 2025-04-10

### Fixed
- **Browser Freezing Issues** - Fixed long-running session performance degradation
  - Limited thinking content to 50KB to prevent unbounded memory growth
  - Limited toolCalls and toolResults to 20 items per message
  - Limited persisted conversations to 10 in localStorage
  - Optimized event delegation for code block copy buttons
  - Removed AnimatePresence popLayout causing layout thrashing

### Changed
- Improved memory management in chat store
- Optimized message component rendering

## [0.3.0] - 2024-04-04

### Added
- Agent system with full lifecycle management
- Plan Mode for implementation planning
- Fork subagent mechanism
- Web search and fetch tools
- Team and task management tools
- Notebook editing tools
- User interaction tools (AskUserQuestion)

## [0.2.0] - 2024-04-02

### Added
- FastAPI backend with RESTful endpoints
- OpenAI and Anthropic LLM provider support
- Streaming chat completion with SSE
- Tool registry system
- File operation tools (read, write, edit)
- Search tools (glob, grep)
- Bash command execution

## [0.1.0] - 2024-04-01

### Added
- Initial project setup
- React frontend with TypeScript
- Zustand state management
- Tailwind CSS styling
- Basic chat UI components

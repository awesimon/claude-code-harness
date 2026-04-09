import { create } from 'zustand';
import type { Task } from '@/types';
import * as api from '@/lib/api';

interface TaskState {
  tasks: Task[];
  isLoading: boolean;
  error: string | null;
  selectedTaskId: string | null;
}

interface TaskActions {
  // CRUD operations
  loadTasks: (filters?: { conversation_id?: string; status?: string; owner?: string }) => Promise<void>;
  createTask: (task: {
    subject: string;
    description: string;
    conversation_id?: string;
    active_form?: string;
    owner?: string;
    status?: 'pending' | 'in_progress' | 'completed';
    blocks?: string[];
    blocked_by?: string[];
    meta?: Record<string, any>;
  }) => Promise<Task>;
  updateTask: (taskId: string, updates: Partial<Task>) => Promise<void>;
  deleteTask: (taskId: string) => Promise<void>;

  // Task lifecycle
  claimTask: (taskId: string, agentId: string, checkAgentBusy?: boolean) => Promise<boolean>;
  unassignTask: (taskId: string) => Promise<void>;
  completeTask: (taskId: string) => Promise<void>;
  startTask: (taskId: string) => Promise<void>;

  // Dependencies
  blockTask: (fromTaskId: string, toTaskId: string) => Promise<void>;

  // Selection
  selectTask: (taskId: string | null) => void;

  // Getters
  getTaskById: (taskId: string) => Task | undefined;
  getTasksByStatus: (status: Task['status']) => Task[];
  getTasksByOwner: (owner: string) => Task[];
  getBlockedTasks: () => Task[];
  getAvailableTasks: () => Task[];
}

export const useTaskStore = create<TaskState & TaskActions>()(
  (set, get) => ({
    // State
    tasks: [],
    isLoading: false,
    error: null,
    selectedTaskId: null,

    // Actions
    loadTasks: async (filters) => {
      set({ isLoading: true, error: null });
      try {
        const tasks = await api.listTasks(filters);
        set({ tasks });
      } catch (error) {
        console.error('Failed to load tasks:', error);
        set({ error: 'Failed to load tasks' });
      } finally {
        set({ isLoading: false });
      }
    },

    createTask: async (taskData) => {
      try {
        const task = await api.createTask(taskData);
        set((state) => ({
          tasks: [task, ...state.tasks],
        }));
        return task;
      } catch (error) {
        console.error('Failed to create task:', error);
        set({ error: 'Failed to create task' });
        throw error;
      }
    },

    updateTask: async (taskId, updates) => {
      try {
        const updatedTask = await api.updateTask(taskId, updates);
        set((state) => ({
          tasks: state.tasks.map((t) =>
            t.id === taskId ? updatedTask : t
          ),
        }));
      } catch (error) {
        console.error('Failed to update task:', error);
        set({ error: 'Failed to update task' });
      }
    },

    deleteTask: async (taskId) => {
      try {
        await api.deleteTask(taskId);
        set((state) => ({
          tasks: state.tasks.filter((t) => t.id !== taskId),
          selectedTaskId: state.selectedTaskId === taskId ? null : state.selectedTaskId,
        }));
      } catch (error) {
        console.error('Failed to delete task:', error);
        set({ error: 'Failed to delete task' });
      }
    },

    claimTask: async (taskId, agentId, checkAgentBusy = false) => {
      try {
        const result = await api.claimTask(taskId, agentId, checkAgentBusy);

        if (result.success && result.task) {
          set((state) => ({
            tasks: state.tasks.map((t) =>
              t.id === taskId ? result.task! : t
            ),
          }));
          return true;
        } else {
          console.log('Failed to claim task:', result.reason);
          return false;
        }
      } catch (error) {
        console.error('Failed to claim task:', error);
        set({ error: 'Failed to claim task' });
        return false;
      }
    },

    unassignTask: async (taskId) => {
      try {
        const task = await api.unassignTask(taskId);
        set((state) => ({
          tasks: state.tasks.map((t) =>
            t.id === taskId ? task : t
          ),
        }));
      } catch (error) {
        console.error('Failed to unassign task:', error);
        set({ error: 'Failed to unassign task' });
      }
    },

    completeTask: async (taskId) => {
      await get().updateTask(taskId, { status: 'completed' });
    },

    startTask: async (taskId) => {
      await get().updateTask(taskId, { status: 'in_progress' });
    },

    blockTask: async (fromTaskId, toTaskId) => {
      try {
        await api.blockTask(fromTaskId, toTaskId);
        // Reload tasks to get updated dependencies
        await get().loadTasks();
      } catch (error) {
        console.error('Failed to block task:', error);
        set({ error: 'Failed to block task' });
      }
    },

    selectTask: (taskId) => {
      set({ selectedTaskId: taskId });
    },

    getTaskById: (taskId) => {
      return get().tasks.find((t) => t.id === taskId);
    },

    getTasksByStatus: (status) => {
      return get().tasks.filter((t) => t.status === status);
    },

    getTasksByOwner: (owner) => {
      return get().tasks.filter((t) => t.owner === owner);
    },

    getBlockedTasks: () => {
      const { tasks } = get();
      const taskIds = new Set(tasks.map((t) => t.id));

      return tasks.filter((task) => {
        // Task is blocked if any of its blocked_by tasks are not completed
        return task.blocked_by?.some((blockerId) => {
          const blocker = tasks.find((t) => t.id === blockerId);
          return blocker && blocker.status !== 'completed';
        });
      });
    },

    getAvailableTasks: () => {
      const { tasks } = get();

      return tasks.filter((task) => {
        // Must be pending
        if (task.status !== 'pending') return false;

        // Must not be blocked
        const isBlocked = task.blocked_by?.some((blockerId) => {
          const blocker = tasks.find((t) => t.id === blockerId);
          return blocker && blocker.status !== 'completed';
        });

        return !isBlocked;
      });
    },
  })
);

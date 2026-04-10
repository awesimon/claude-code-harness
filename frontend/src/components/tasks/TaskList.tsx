import React, { useEffect, useState } from 'react';
import { useTaskStore } from '@/stores/taskStore';
import type { Task } from '@/types';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';

interface TaskListProps {
  conversationId?: string;
}

const statusColors: Record<Task['status'], 'default' | 'secondary' | 'success' | 'warning' | 'destructive'> = {
  pending: 'default',
  in_progress: 'secondary',
  completed: 'success',
};

const statusLabels: Record<Task['status'], string> = {
  pending: '待处理',
  in_progress: '进行中',
  completed: '已完成',
};

export const TaskList: React.FC<TaskListProps> = ({ conversationId }) => {
  const {
    tasks,
    isLoading,
    selectedTaskId,
    loadTasks,
    createTask,
    updateTask,
    deleteTask,
    claimTask,
    completeTask,
    selectTask,
  } = useTaskStore();

  const [newTaskSubject, setNewTaskSubject] = useState('');
  const [newTaskDescription, setNewTaskDescription] = useState('');
  const [agentId, setAgentId] = useState('current-user');

  useEffect(() => {
    loadTasks(conversationId ? { conversation_id: conversationId } : undefined);
  }, [conversationId, loadTasks]);

  const handleCreateTask = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTaskSubject.trim()) return;

    await createTask({
      subject: newTaskSubject,
      description: newTaskDescription,
      conversation_id: conversationId,
      status: 'pending',
    });

    setNewTaskSubject('');
    setNewTaskDescription('');
  };

  const handleClaimTask = async (taskId: string) => {
    await claimTask(taskId, agentId);
  };

  const handleCompleteTask = async (taskId: string) => {
    await completeTask(taskId);
  };

  const handleDeleteTask = async (taskId: string) => {
    if (confirm('确定要删除这个任务吗？')) {
      await deleteTask(taskId);
    }
  };

  if (isLoading) {
    return <div className="p-4 text-center">加载中...</div>;
  }

  return (
    <div className="space-y-4">
      {/* Create Task Form */}
      <form onSubmit={handleCreateTask} className="space-y-2 p-4 bg-muted rounded-lg">
        <h3 className="font-medium">创建新任务</h3>
        <input
          type="text"
          placeholder="任务标题"
          value={newTaskSubject}
          onChange={(e) => setNewTaskSubject(e.target.value)}
          className="w-full px-3 py-2 border rounded-md text-sm bg-background"
        />
        <textarea
          placeholder="任务描述"
          value={newTaskDescription}
          onChange={(e) => setNewTaskDescription(e.target.value)}
          className="w-full px-3 py-2 border rounded-md text-sm h-20 resize-none bg-background"
        />
        <Button type="submit" size="sm" className="w-full">
          创建任务
        </Button>
      </form>

      {/* Task List */}
      <div className="space-y-2">
        {tasks.length === 0 ? (
          <div className="text-center text-muted-foreground py-8">暂无任务</div>
        ) : (
          tasks.map((task) => (
            <div
              key={task.id}
              onClick={() => selectTask(task.id === selectedTaskId ? null : task.id)}
              className={`p-3 border rounded-lg cursor-pointer transition-colors ${
                selectedTaskId === task.id ? 'border-foreground bg-muted' : 'border-border hover:bg-muted'
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="font-medium truncate">#{task.id.slice(0, 8)} {task.subject}</span>
                    <Badge variant={statusColors[task.status]} size="sm">
                      {statusLabels[task.status]}
                    </Badge>
                  </div>
                  <p className="text-sm text-muted-foreground mt-1 line-clamp-2">{task.description}</p>
                  {task.owner && (
                    <p className="text-xs text-muted-foreground mt-1">负责人: {task.owner}</p>
                  )}
                  {task.blocked_by && task.blocked_by.length > 0 && (
                    <p className="text-xs text-muted-foreground mt-1">
                      被阻塞: {task.blocked_by.join(', ')}
                    </p>
                  )}
                </div>
              </div>

              {/* Task Actions */}
              {selectedTaskId === task.id && (
                <div className="flex gap-2 mt-3 pt-3 border-t">
                  {task.status === 'pending' && !task.owner && (
                    <Button
                      size="sm"
                      variant="default"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleClaimTask(task.id);
                      }}
                    >
                      认领
                    </Button>
                  )}
                  {task.status === 'in_progress' && (
                    <Button
                      size="sm"
                      variant="default"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleCompleteTask(task.id);
                      }}
                    >
                      完成
                    </Button>
                  )}
                  {task.status !== 'completed' && (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={(e) => {
                        e.stopPropagation();
                        handleDeleteTask(task.id);
                      }}
                    >
                      删除
                    </Button>
                  )}
                </div>
              )}
            </div>
          ))
        )}
      </div>

      {/* Task Stats */}
      <div className="flex gap-4 text-sm text-muted-foreground pt-4 border-t">
        <span>总计: {tasks.length}</span>
        <span>待处理: {tasks.filter((t) => t.status === 'pending').length}</span>
        <span>进行中: {tasks.filter((t) => t.status === 'in_progress').length}</span>
        <span>已完成: {tasks.filter((t) => t.status === 'completed').length}</span>
      </div>
    </div>
  );
};

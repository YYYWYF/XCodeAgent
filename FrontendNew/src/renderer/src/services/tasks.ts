import { taskDetails } from '../mocks/tasks'
import type { TaskDetail } from '../types/task'

export const getTaskDetailById = (taskId: string): TaskDetail | undefined => taskDetails[taskId]

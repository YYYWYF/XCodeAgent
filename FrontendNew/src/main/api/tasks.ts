import { backendHttp, createApiError } from '../http'
import type { TaskDetail, TaskMenuItem } from '../../shared/task'

const encodePathSegment = (value: string): string => encodeURIComponent(value.trim())

export const listTasks = async (): Promise<TaskMenuItem[]> => {
  const response = await backendHttp.get<TaskMenuItem[]>('/tasks')

  return response.data
}

export const getTaskDetail = async (taskId: string): Promise<TaskDetail> => {
  if (!taskId.trim()) {
    throw createApiError('INVALID_TASK_ID', 'Task id is required')
  }

  const response = await backendHttp.get<TaskDetail>(`/tasks/${encodePathSegment(taskId)}`)

  return response.data
}

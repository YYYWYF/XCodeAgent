export type TaskStatusTone = 'success' | 'warning'

export type TaskMenuItem = {
  id: string
  title: string
  statusTone: TaskStatusTone
}

export type TaskDetail = {
  id: string
  title: string
  status: string
  statusTone: TaskStatusTone
  description: string
  owner: string
  createdAt: string
  updatedAt: string
  checklist: string[]
}

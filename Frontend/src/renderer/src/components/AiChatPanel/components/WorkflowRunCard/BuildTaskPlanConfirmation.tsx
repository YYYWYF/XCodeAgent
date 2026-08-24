import {
  CodeOutlined,
  EditOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SaveOutlined
} from '@ant-design/icons'
import { Alert, Button, Collapse, Input, Space, Typography } from 'antd'
import { useEffect, useMemo, useState } from 'react'
import type { Dispatch, SetStateAction } from 'react'
import type {
  WorkflowBuildTaskPlan,
  WorkflowBuildTaskPlanConfirmation,
  WorkflowBuildTaskPlanTask
} from '../../../../typings'
import { cx } from '../../../../utils'

import './BuildTaskPlanConfirmation.less'

const { TextArea } = Input

type BuildTaskPlanConfirmationProps = {
  disabled?: boolean
  plan?: WorkflowBuildTaskPlan
  errors?: string[]
  onSubmit: (action: WorkflowBuildTaskPlanConfirmation) => void
}

type TaskDraft = {
  title: string
  description: string
}

const TASK_TONES = ['green', 'purple', 'blue', 'orange'] as const
type TaskTone = (typeof TASK_TONES)[number]

/** 展示待确认的 Build DAG，并将可编辑字段限制在任务名称和描述。 */
export default function BuildTaskPlanConfirmation({
  disabled,
  plan,
  errors,
  onSubmit
}: BuildTaskPlanConfirmationProps): JSX.Element {
  // 稳定任务数组引用，避免无关渲染触发草稿同步逻辑。
  const taskList = plan?.tasks
  const tasks = useMemo(() => (Array.isArray(taskList) ? taskList : []), [taskList])
  const taskFingerprint = useMemo(
    () => tasks.map((task) => `${task.id}:${task.title}:${task.description}`).join('|'),
    [tasks]
  )
  const taskToneById = useMemo(
    () => new Map(tasks.map((task, index) => [task.id, taskTone(index)])),
    [tasks]
  )
  const semanticUnitLabels = useMemo(
    () => [...new Set(tasks.map((task) => taskUnitLabel(task.unit_id)))],
    [tasks]
  )
  const dependencyCount = useMemo(
    () => tasks.reduce((count, task) => count + (task.dependencies?.length || 0), 0),
    [tasks]
  )
  const [drafts, setDrafts] = useState<Record<string, TaskDraft>>(() => taskDrafts(tasks))

  useEffect(() => {
    setDrafts(taskDrafts(tasks))
  }, [taskFingerprint, tasks])

  const changedPatches = tasks.flatMap((task) => {
    const draft = drafts[task.id]
    if (!draft || (draft.title === task.title && draft.description === task.description)) return []
    return [
      {
        task_id: task.id,
        title: draft.title,
        description: draft.description
      }
    ]
  })

  return (
    <div className={cx('workflow-dag-confirmation')}>
      <div className={cx('workflow-dag-confirmation-overview')}>
        <div className={cx('workflow-dag-confirmation-overview-copy')}>
          <span className={cx('workflow-dag-confirmation-overview-icon')}>
            <CodeOutlined />
          </span>
          <div className={cx('workflow-dag-confirmation-overview-text')}>
            <div className={cx('workflow-dag-confirmation-overview-title')}>
              <Typography.Text strong>Build DAG 待确认</Typography.Text>
            </div>
            <Typography.Text
              className={cx('workflow-dag-confirmation-overview-subtitle')}
              type="secondary"
            >
              共 {tasks.length} 个任务 · {semanticUnitLabels.length} 类任务单元 · {dependencyCount}{' '}
              条依赖 · 需确认后进入 Build 流程
            </Typography.Text>
          </div>
        </div>
      </div>
      {errors && errors.length > 0 ? (
        <Alert
          className={cx('workflow-dag-confirmation-errors')}
          description={errors.join('；')}
          message="需要先处理以下问题"
          showIcon
          type="warning"
        />
      ) : null}
      {tasks.length === 0 ? (
        <div className={cx('workflow-dag-confirmation-empty')}>暂无可确认的任务</div>
      ) : null}
      <div className={cx('workflow-dag-confirmation-task-section-heading')}>
        <div>
          <Typography.Text strong>任务详情</Typography.Text>
        </div>
        <span className={cx('workflow-dag-confirmation-unit-count')}>
          {semanticUnitLabels.length} 类任务单元
        </span>
      </div>
      <Collapse
        className={cx('workflow-dag-confirmation-tasks')}
        defaultActiveKey={tasks.map((task) => task.id)}
      >
        {tasks.map((task, index) => {
          const draft = drafts[task.id] || { title: task.title, description: task.description }
          const dependencies = task.dependencies || []
          const tone = taskToneById.get(task.id) || 'purple'
          return (
            <Collapse.Panel
              header={
                <div className={cx('workflow-dag-confirmation-task-header')}>
                  <div className={cx('workflow-dag-confirmation-task-header-main')}>
                    <span
                      className={cx(
                        'workflow-dag-confirmation-task-index',
                        `workflow-dag-confirmation-tone-${tone}`
                      )}
                    >
                      {index + 1}
                    </span>
                    <span
                      className={cx(
                        'workflow-dag-confirmation-task-id',
                        `workflow-dag-confirmation-tone-${tone}`
                      )}
                    >
                      {task.id}
                    </span>
                    <span
                      className={cx('workflow-dag-confirmation-task-unit')}
                      title={task.unit_id || 'application:root'}
                    >
                      <span className={cx('workflow-dag-confirmation-task-unit-label')}>
                        任务类型
                      </span>
                      <span className={cx('workflow-dag-confirmation-task-unit-value')}>
                        {taskUnitLabel(task.unit_id)}
                      </span>
                    </span>
                  </div>
                  <div className={cx('workflow-dag-confirmation-task-dependencies')}>
                    <span className={cx('workflow-dag-confirmation-task-dependency-label')}>
                      前置任务
                    </span>
                    {dependencies.length > 0 ? (
                      dependencies.map((dependency) => {
                        const dependencyTone = taskToneById.get(dependency) || 'purple'
                        return (
                          <span
                            className={cx(
                              'workflow-dag-confirmation-dependency-chip',
                              `workflow-dag-confirmation-tone-${dependencyTone}`
                            )}
                            key={dependency}
                          >
                            {dependency}
                          </span>
                        )
                      })
                    ) : (
                      <span className={cx('workflow-dag-confirmation-task-dependency-empty')}>
                        无
                      </span>
                    )}
                  </div>
                </div>
              }
              key={task.id}
            >
              <div className={cx('workflow-dag-confirmation-edit-grid')}>
                <label>
                  <span className={cx('workflow-dag-confirmation-field-label')}>任务名称</span>
                  <span className={cx('workflow-dag-confirmation-field-control')}>
                    <Input
                      disabled={disabled}
                      size="small"
                      value={draft.title}
                      onChange={(event) =>
                        updateDraft(task.id, 'title', event.target.value, setDrafts)
                      }
                    />
                    <EditOutlined aria-hidden="true" />
                  </span>
                </label>
                <label>
                  <span className={cx('workflow-dag-confirmation-field-label')}>任务描述</span>
                  <span className={cx('workflow-dag-confirmation-field-control')}>
                    <TextArea
                      autoSize={{ minRows: 1, maxRows: 3 }}
                      disabled={disabled}
                      size="small"
                      value={draft.description}
                      onChange={(event) =>
                        updateDraft(task.id, 'description', event.target.value, setDrafts)
                      }
                    />
                    <EditOutlined aria-hidden="true" />
                  </span>
                </label>
              </div>
              <div className={cx('workflow-dag-confirmation-readonly')}>
                <div className={cx('workflow-dag-confirmation-readonly-item')}>
                  <span className={cx('workflow-dag-confirmation-readonly-label')}>范围</span>
                  <Typography.Text
                    className={cx('workflow-dag-confirmation-readonly-value')}
                    title={formatTaskPaths(task)}
                  >
                    {formatTaskPaths(task)}
                  </Typography.Text>
                </div>
                <div className={cx('workflow-dag-confirmation-readonly-item')}>
                  <span className={cx('workflow-dag-confirmation-readonly-label')}>验收</span>
                  <Typography.Text className={cx('workflow-dag-confirmation-readonly-value')}>
                    工程 {task.acceptance_checks?.length || 0} 项 · 业务{' '}
                    {task.business_acceptance_checks?.length || 0} 项
                  </Typography.Text>
                </div>
              </div>
            </Collapse.Panel>
          )
        })}
      </Collapse>
      <div className={cx('workflow-dag-confirmation-actions')}>
        <Space size={6} wrap>
          <Button
            disabled={disabled}
            icon={<ReloadOutlined />}
            onClick={() => onSubmit({ mode: 'build_task_plan_confirmation', action: 'regenerate' })}
            size="small"
          >
            重新生成
          </Button>
          <Button
            disabled={disabled || changedPatches.length === 0}
            icon={<SaveOutlined />}
            onClick={() =>
              onSubmit({
                mode: 'build_task_plan_confirmation',
                action: 'patch',
                patches: changedPatches
              })
            }
            size="small"
            type="default"
          >
            保存修改
          </Button>
          <Button
            disabled={disabled || tasks.length === 0}
            icon={<PlayCircleOutlined />}
            onClick={() => onSubmit({ mode: 'build_task_plan_confirmation', action: 'confirm' })}
            size="small"
            type="primary"
          >
            确认并进入 Build
          </Button>
        </Space>
      </div>
    </div>
  )
}

/** 按任务在当前 DAG 中的顺序分配稳定色标，保证依赖 ID 前后一致。 */
function taskTone(index: number): TaskTone {
  return TASK_TONES[index % TASK_TONES.length]
}

/** 将内部 Unit 前缀映射为用户可以直接理解的任务单元文案。 */
function taskUnitLabel(unitId?: string): string {
  const normalizedUnitId = String(unitId || 'application:root')
    .trim()
    .toLowerCase()
  if (normalizedUnitId.startsWith('page:')) return '前端页面'
  if (normalizedUnitId.startsWith('backend:endpoint:')) return '后端接口'
  if (normalizedUnitId.startsWith('backend:')) return '后端公共能力'
  if (normalizedUnitId.startsWith('frontend:data:')) return '前端数据'
  if (normalizedUnitId.startsWith('frontend:')) return '前端公共能力'
  if (normalizedUnitId.startsWith('database:')) return '数据库'
  if (normalizedUnitId === 'application:root') return '应用公共能力'
  return '应用任务'
}

/** 将后端任务投影为可编辑草稿，避免直接修改 Workflow 快照。 */
function taskDrafts(tasks: WorkflowBuildTaskPlanTask[]): Record<string, TaskDraft> {
  return Object.fromEntries(
    tasks.map((task) => [task.id, { title: task.title || '', description: task.description || '' }])
  )
}

/** 只更新当前任务的 title 或 description 草稿。 */
function updateDraft(
  taskId: string,
  field: keyof TaskDraft,
  value: string,
  setDrafts: Dispatch<SetStateAction<Record<string, TaskDraft>>>
): void {
  setDrafts((current) => ({
    ...current,
    [taskId]: {
      ...(current[taskId] || { title: '', description: '' }),
      [field]: value
    }
  }))
}

/** 组合任务声明的只读路径范围，供用户核对任务边界。 */
function formatTaskPaths(task: WorkflowBuildTaskPlanTask): string {
  const paths = [
    ...(task.target_files || []),
    ...(task.allowed_paths || []),
    ...(task.change_scope || []).flatMap((item) =>
      typeof item.path === 'string' ? [item.path] : []
    )
  ]
  return [...new Set(paths)].join('、') || '未声明'
}

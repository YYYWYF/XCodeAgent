import {
  CheckCircleOutlined,
  CloseCircleOutlined,
  GlobalOutlined,
  LoadingOutlined,
  RocketOutlined
} from '@ant-design/icons'
import { Button, message, Typography } from 'antd'
import { useMemo } from 'react'
import type { ReactElement } from 'react'
import type {
  WorkflowLaunchProgress,
  WorkflowLaunchResult,
  WorkflowRunPayload
} from '../../../../typings'
import { cx } from '../../../../utils'
import { normalizePreviewUrl, openPreviewWindow } from '../../../../utils/previewUrl'
import { launchStageIndex, projectLaunchProgress } from './projectLaunchProgress'
import './ProjectLaunchCard.less'

const { Text } = Typography

type Props = {
  workflow: WorkflowRunPayload
}

type StepState = 'pending' | 'running' | 'completed' | 'failed'

const LAUNCH_STEPS = [
  { key: 'detect', label: '识别工程结构' },
  { key: 'backend', label: '启动后端服务' },
  { key: 'frontend', label: '启动前端服务' },
  { key: 'ready', label: '健康检查就绪' }
] as const

/** 后端失败阶段到步骤序号的映射，用于失败时高亮具体步骤。 */
const FAILED_STEP_INDEX: Record<string, number> = {
  backend_validation: 0,
  backend_database_config: 1,
  backend_cleanup: 1,
  backend_build: 1,
  backend_jar: 1,
  backend_repackage: 1,
  backend_start: 1,
  frontend_start: 2
}

/** 后端失败阶段代码到中文阶段名的映射。 */
const FAILED_STAGE_LABELS: Record<string, string> = {
  backend_validation: '后端环境检查',
  backend_database_config: '数据库配置',
  backend_cleanup: '旧进程清理',
  backend_build: '后端构建',
  backend_jar: '后端打包产物',
  backend_repackage: '后端补打包',
  backend_start: '后端启动',
  frontend_start: '前端启动'
}

/** 展示项目启动节点的运行、完成与失败状态，包含预览入口和错误详情。 */
export default function ProjectLaunchCard({ workflow }: Props): ReactElement {
  const status = String(workflow.summary.status || '')
  const launch = workflow.summary.launchResult
  const previewUrl = normalizePreviewUrl(String(workflow.summary.previewUrl || ''))
  const running = status === 'running'
  const failed = status === 'failed'
  const completed = !running && !failed
  const launchProgress = projectLaunchProgress(workflow)
  const stepStates = useMemo(
    () => resolveStepStates(launch, status, launchProgress),
    [launch, status, launchProgress]
  )
  const failedStage = String(launch?.failed_stage || '')

  const handleOpenPreview = async (): Promise<void> => {
    if (!previewUrl) return
    try {
      await openPreviewWindow(previewUrl)
    } catch (error) {
      message.error(error instanceof Error ? error.message : '打开预览窗口失败')
    }
  }

  return (
    <section
      className={cx(
        'project-launch-card',
        running && 'running',
        completed && 'completed',
        failed && 'failed'
      )}
    >
      <header className={cx('project-launch-header')}>
        <span className={cx('project-launch-icon')} aria-hidden="true">
          {running ? (
            <LoadingOutlined spin />
          ) : failed ? (
            <CloseCircleOutlined />
          ) : (
            <CheckCircleOutlined />
          )}
        </span>
        <div className={cx('project-launch-copy')}>
          <Text strong>
            {running ? '正在启动项目预览' : failed ? '项目启动失败' : '项目启动完成'}
          </Text>
          <Text type="secondary">
            {running
              ? launchProgress?.message || '正在启动本地预览服务，请稍候…'
              : failed
                ? String(launch?.message || workflow.summary.message || '启动过程发生错误。')
                : String(launch?.message || '项目已启动，可以开始预览。')}
          </Text>
        </div>
        <span
          className={cx(
            'project-launch-status',
            running && 'running',
            completed && 'completed',
            failed && 'failed'
          )}
        >
          {running ? '启动中' : failed ? '启动失败' : '已就绪'}
        </span>
      </header>

      <ol className={cx('project-launch-steps')}>
        {LAUNCH_STEPS.map((step, index) => (
          <li className={cx('project-launch-step', stepStates[index])} key={step.key}>
            <span className={cx('project-launch-step-icon')} aria-hidden="true">
              {stepStateIcon(stepStates[index])}
            </span>
            <Text className={cx('project-launch-step-label')}>{step.label}</Text>
            <Text className={cx('project-launch-step-state')} type="secondary">
              {stepStateText(stepStates[index])}
            </Text>
          </li>
        ))}
      </ol>

      {completed && previewUrl && (
        <div className={cx('project-launch-preview')}>
          <span className={cx('project-launch-preview-icon')} aria-hidden="true">
            <GlobalOutlined />
          </span>
          <Text className={cx('project-launch-preview-url')} code>
            {previewUrl}
          </Text>
          <Button icon={<RocketOutlined />} onClick={() => void handleOpenPreview()} type="primary">
            打开预览
          </Button>
        </div>
      )}

      {failed && (
        <div className={cx('project-launch-error')}>
          <span className={cx('project-launch-error-icon')} aria-hidden="true">
            <CloseCircleOutlined />
          </span>
          <div className={cx('project-launch-error-copy')}>
            <Text strong>{FAILED_STAGE_LABELS[failedStage] || '未知阶段'}启动失败</Text>
            <Text>{String(launch?.message || '未提供错误详情。')}</Text>
            {launch?.backend &&
            String(launch.backend.status) === 'failed' &&
            launch.backend.message ? (
              <Text type="secondary">{String(launch.backend.message)}</Text>
            ) : null}
            {launch?.frontend &&
            String(launch.frontend.status) === 'failed' &&
            launch.frontend.message ? (
              <Text type="secondary">{String(launch.frontend.message)}</Text>
            ) : null}
          </div>
        </div>
      )}
    </section>
  )
}

/** 根据启动结果、Workflow 状态与实时进度事件推导四个步骤的展示状态。 */
function resolveStepStates(
  launch: WorkflowLaunchResult | undefined,
  status: string,
  progress?: WorkflowLaunchProgress
): StepState[] {
  if (status === 'running') {
    const stageIndex = launchStageIndex(progress?.stage)
    if (stageIndex !== undefined) {
      const stageStatus = String(progress?.status || 'running')
      return LAUNCH_STEPS.map((_, index) =>
        index < stageIndex
          ? 'completed'
          : index === stageIndex
            ? stageStatus === 'failed'
              ? 'failed'
              : stageStatus === 'running'
                ? 'running'
                : 'completed'
            : 'pending'
      )
    }
    return ['running', 'pending', 'pending', 'pending']
  }
  if (status === 'failed') {
    const failedIndex = FAILED_STEP_INDEX[String(launch?.failed_stage || '')] ?? 0
    return LAUNCH_STEPS.map((_, index) =>
      index < failedIndex ? 'completed' : index === failedIndex ? 'failed' : 'pending'
    )
  }
  return ['completed', 'completed', 'completed', 'completed']
}

/** 返回步骤状态对应的图标或占位圆点。 */
function stepStateIcon(state: StepState): ReactElement {
  if (state === 'running') return <LoadingOutlined spin />
  if (state === 'completed') return <CheckCircleOutlined />
  if (state === 'failed') return <CloseCircleOutlined />
  return <span className={cx('project-launch-step-dot')} />
}

/** 返回步骤状态的中文文案。 */
function stepStateText(state: StepState): string {
  if (state === 'running') return '运行中'
  if (state === 'completed') return '已完成'
  if (state === 'failed') return '失败'
  return '等待中'
}

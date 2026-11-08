import { Alert, Button, Progress } from 'antd'
import { CheckCircleFilled, ClockCircleOutlined } from '@ant-design/icons'
import type { ReactElement } from 'react'
import type { PageArtifactOverview } from '../../../../service/pageArtifacts'
import { cx } from '../../../../utils'
import './PageArtifactTasks.less'

const TASK_LABELS: Record<string, string> = {
  completed: '已完成',
  already_satisfied: '已满足',
  running: '执行中',
  pending: '待执行',
  failed: '失败',
  blocked: '被阻塞',
  skipped: '已跳过',
  cancelled: '已取消',
  unknown: '状态未记录'
}

/** 根据真实执行状态选择色彩，跳过、未知和取消不计作完成。 */
function taskClass(status: string): string {
  if (['completed', 'already_satisfied'].includes(status)) return 'done'
  return ['running', 'failed'].includes(status) ? status : 'pending'
}

/** 展示正式任务及其原始说明，任务计数与页面开发状态独立。 */
export default function PageArtifactTasks({
  overview,
  onReload
}: {
  overview: PageArtifactOverview
  onReload: () => void
}): ReactElement {
  const { tasks, issues, taskPlanAvailable } = overview
  const completed = tasks.filter((task) =>
    ['completed', 'already_satisfied'].includes(task.status)
  ).length
  const satisfied = tasks.filter((task) => task.status === 'already_satisfied').length
  const running = tasks.filter((task) => task.status === 'running').length
  const failed = tasks.filter((task) => task.status === 'failed').length
  return (
    <section className={cx('page-artifact-task-section')} aria-label="开发任务">
      <div className={cx('page-artifact-section-heading')}>
        <h3>开发任务</h3>
        {!issues.tasks && tasks.length > 0 && (
          <span className={cx('page-artifact-task-count')}>
            {completed} / {tasks.length} {satisfied ? '已完成或满足' : '已完成'}
          </span>
        )}
      </div>
      <p className={cx('page-artifact-caption')}>
        {taskPlanAvailable ? '来源：已确认开发计划' : '任务以正式确认后的开发计划为准'}
      </p>
      {issues.tasks ? (
        <Alert
          type="error"
          showIcon
          message="开发任务读取失败"
          description={issues.tasks}
          action={<Button onClick={onReload}>重试</Button>}
        />
      ) : tasks.length ? (
        <>
          <Progress
            percent={Math.round((completed / tasks.length) * 100)}
            showInfo={false}
            strokeWidth={4}
            strokeColor="var(--wb-accent)"
            trailColor="var(--wb-border)"
          />
          {(running > 0 || failed > 0) && (
            <p className={cx('page-artifact-caption')}>
              执行中 {running} · 失败 {failed}
            </p>
          )}
          <div className={cx('page-artifact-tasks')}>
            {tasks.map((task) => (
              <details
                className={cx('page-artifact-task')}
                key={task.id}
                open={tasks.length === 1 ? true : undefined}
              >
                <summary>
                  <span className={cx('page-artifact-task-icon', taskClass(task.status))}>
                    {['completed', 'already_satisfied'].includes(task.status) ? (
                      <CheckCircleFilled />
                    ) : (
                      <ClockCircleOutlined />
                    )}
                  </span>
                  <span className={cx('page-artifact-task-title')}>{task.title}</span>
                  <span className={cx('page-artifact-task-scope')}>
                    {task.scope === 'page' ? '本页任务' : '前置依赖任务'}
                  </span>
                  <span className={cx('page-artifact-task-status', taskClass(task.status))}>
                    {TASK_LABELS[task.status] || `未识别状态：${task.status}`}
                  </span>
                </summary>
                <div className={cx('page-artifact-task-detail')}>
                  <p>{task.description || '任务未记录详细说明。'}</p>
                  {task.failureReason && (
                    <Alert type="error" showIcon message={task.failureReason} />
                  )}
                  {task.dependencies.length > 0 && (
                    <p className={cx('page-artifact-caption')}>
                      依赖任务：{task.dependencies.map((dependency) => dependency.title).join('、')}
                    </p>
                  )}
                  {(task.owner || task.updatedAt) && (
                    <div className={cx('page-artifact-task-meta')}>
                      {task.owner && (
                        <span>
                          执行方 <b>{task.owner}</b>
                        </span>
                      )}
                      {task.updatedAt && (
                        <span>更新于 {new Date(task.updatedAt).toLocaleString()}</span>
                      )}
                    </div>
                  )}
                </div>
              </details>
            ))}
          </div>
          <p className={cx('page-artifact-caption', 'page-artifact-task-note')}>
            包含本页及前置依赖任务；任务完成情况与页面开发状态分别记录。
          </p>
        </>
      ) : (
        <p className={cx('page-artifact-empty')}>
          {taskPlanAvailable
            ? '当前正式开发计划尚未包含此页面的任务。'
            : '尚未生成并确认开发任务。'}
        </p>
      )}
    </section>
  )
}

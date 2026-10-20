import {
  ApiOutlined,
  ArrowRightOutlined,
  FileTextOutlined,
  SearchOutlined
} from '@ant-design/icons'
import { Button, Empty, Input, Select, Typography } from 'antd'
import { useState, type ReactElement } from 'react'
import { cx } from '../../../../utils'
import { developmentStatusText } from '../../../../developmentArtifacts'
import type { QuickTaskItem } from '../QuickTaskGuide/quickTasks'
import {
  filterDevelopmentTargets,
  type TargetKindFilter,
  type TargetStatusFilter
} from './pickerTasks'

const { Text } = Typography
const statuses = [
  ['all', '全部'],
  ['pending', '未开发'],
  ['in_progress', '开发中'],
  ['completed', '已完成']
] as const

type Props = {
  tasks: QuickTaskItem[]
  blocked: boolean
  startingId: string
  onStart: (task: QuickTaskItem) => Promise<void>
}

/** 展示完整开发目录，筛选仅改变列表，不改变上方推荐和正式开发状态。 */
export default function DevelopmentTargetPicker({
  tasks,
  blocked,
  startingId,
  onStart
}: Props): ReactElement {
  const [search, setSearch] = useState('')
  const [status, setStatus] = useState<TargetStatusFilter>('all')
  const [kind, setKind] = useState<TargetKindFilter>('all')
  const targets = tasks.filter((task) => task.kind !== 'entity')
  const completed = targets.filter(
    (task) => task.progress?.initialDevelopmentStatus === 'completed'
  ).length
  const filtered = filterDevelopmentTargets(targets, status, kind, search)
  return (
    <div className={cx('next-development-guide-picker')}>
      <div className={cx('next-development-guide-picker-toolbar')}>
        <div className={cx('next-development-guide-picker-title')}>
          <Text strong>全部页面与 API</Text>
          <Text type="secondary">
            共 {targets.length} 项 · 已完成 {completed} 项
          </Text>
        </div>
        <Input
          allowClear
          prefix={<SearchOutlined aria-hidden="true" />}
          aria-label="搜索开发目标"
          placeholder="搜索名称或路径"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      </div>
      <div className={cx('next-development-guide-picker-filters')}>
        <div role="group" aria-label="按开发状态筛选">
          {statuses.map(([value, label]) => (
            <Button
              key={value}
              type="text"
              aria-pressed={status === value}
              onClick={() => setStatus(value)}
            >
              {label}{' '}
              <span>
                {value === 'all'
                  ? targets.length
                  : targets.filter((task) => task.progress?.initialDevelopmentStatus === value)
                      .length}
              </span>
            </Button>
          ))}
        </div>
        <Select<TargetKindFilter>
          aria-label="开发目标类型"
          value={kind}
          onChange={setKind}
          options={[
            { value: 'all', label: '全部类型' },
            { value: 'page', label: '页面' },
            { value: 'endpoint', label: 'API' }
          ]}
        />
      </div>
      <Text type="secondary" className={cx('next-development-guide-picker-caption')}>
        展示全部开发目标，只有未开发项可以开始开发
      </Text>
      {(['page', 'endpoint'] as const).map((targetKind) => {
        const items = filtered.filter((task) => task.kind === targetKind)
        return items.length ? (
          <section key={targetKind} aria-label={targetKind === 'page' ? '应用页面' : '应用 API'}>
            <div className={cx('next-development-guide-group-heading')}>
              <Text strong>{targetKind === 'page' ? '应用页面' : '应用 API'}</Text>
              <span>{items.length}</span>
            </div>
            <div className={cx('next-development-guide-items')}>
              {items.map((task) => (
                <div key={task.id} className={cx('next-development-guide-item')}>
                  <span className={cx('next-development-guide-item-icon')} aria-hidden="true">
                    {targetKind === 'page' ? <FileTextOutlined /> : <ApiOutlined />}
                  </span>
                  <div>
                    <Text>{task.title}</Text>
                    <Text type="secondary">
                      {task.kind === 'endpoint' ? `${task.meta} ${task.endpointPath}` : task.meta}
                    </Text>
                  </div>
                  <span
                    className={cx('next-development-guide-item-status')}
                    data-status={task.progress?.initialDevelopmentStatus}
                  >
                    {developmentStatusText(task.progress)}
                  </span>
                  {task.progress?.initialDevelopmentStatus === 'pending' ? (
                    <Button
                      className={cx('next-development-guide-item-action')}
                      size="small"
                      type="text"
                      aria-label={`开始开发「${task.title}」`}
                      disabled={blocked}
                      loading={startingId === task.id}
                      onClick={() => void onStart(task)}
                    >
                      开始开发 <ArrowRightOutlined />
                    </Button>
                  ) : null}
                </div>
              ))}
            </div>
          </section>
        ) : null
      })}
      {!filtered.length ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有匹配的页面或 API" />
      ) : null}
    </div>
  )
}

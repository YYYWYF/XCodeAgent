import {
  CheckCircleOutlined,
  CodeOutlined,
  FolderOpenOutlined,
  RadarChartOutlined,
  WarningOutlined
} from '@ant-design/icons'
import { Typography } from 'antd'
import type { ReactElement } from 'react'
import type {
  WorkspaceInspectionPathItem,
  WorkspaceInspectionSnapshot
} from '../../../../service/agUiAgent'
import { cx } from '../../../../utils'
import './WorkspaceInspectionPanel.less'

const { Text } = Typography

type Props = {
  snapshot: WorkspaceInspectionSnapshot
}

/** 展示工作区静态扫描的结构化摘要和关键识别结果。 */
export default function WorkspaceInspectionPanel({ snapshot }: Props): ReactElement {
  const revision = snapshot.revision.slice(0, 12) || 'UNVERSIONED'
  const metrics = [
    { label: '索引文件', value: snapshot.fileManifest.totalFiles },
    { label: '源文件', value: snapshot.fileManifest.sourceFiles },
    { label: '项目根', value: snapshot.projectRoots.length }
  ]

  return (
    <section className={cx('workspace-inspection')} aria-label="工作区扫描结果">
      <header className={cx('workspace-inspection-header')}>
        <div className={cx('workspace-inspection-identity')}>
          <span className={cx('workspace-inspection-mark')} aria-hidden="true">
            <RadarChartOutlined />
          </span>
          <span className={cx('workspace-inspection-heading')}>
            <Text className={cx('workspace-inspection-eyebrow')}>WORKSPACE SCAN</Text>
            <Text className={cx('workspace-inspection-title')} strong>
              工作区结构扫描
            </Text>
          </span>
        </div>
        <div className={cx('workspace-inspection-status')}>
          <span className={cx('workspace-inspection-status-dot')} aria-hidden="true" />
          <span>{snapshot.cacheHit ? 'CACHE HIT' : 'SNAPSHOT READY'}</span>
          <code>REV {revision}</code>
        </div>
      </header>

      <div className={cx('workspace-inspection-metrics')}>
        {metrics.map((metric, index) => (
          <div className={cx('workspace-inspection-metric')} key={metric.label}>
            <small>{String(index + 1).padStart(2, '0')}</small>
            <strong>{metric.value.toLocaleString()}</strong>
            <span>{metric.label}</span>
          </div>
        ))}
      </div>

      <div className={cx('workspace-inspection-grid')}>
        <section className={cx('workspace-inspection-block', 'stack')}>
          <BlockTitle icon={<CodeOutlined />} title="技术栈识别" />
          <div className={cx('workspace-inspection-tags')}>
            {snapshot.techStack.length > 0 ? (
              snapshot.techStack.map((item) => <span key={item}>{item}</span>)
            ) : (
              <Text type="secondary">未识别到明确技术栈</Text>
            )}
          </div>
        </section>

        <section className={cx('workspace-inspection-block')}>
          <BlockTitle icon={<FolderOpenOutlined />} title="项目结构" />
          <PathList emptyText="未识别到项目根目录" items={snapshot.projectRoots} />
        </section>

        <section className={cx('workspace-inspection-block', 'entrypoints')}>
          <BlockTitle icon={<RadarChartOutlined />} title="入口定位" />
          <PathList emptyText="未识别到已知入口文件" items={snapshot.entrypoints} />
        </section>
      </div>

      <footer className={cx('workspace-inspection-signals')}>
        <InspectionSignal
          warning={!snapshot.codeGraph.available}
          text={
            snapshot.codeGraph.available
              ? `语义图谱已连接 · ${snapshot.codeGraph.provider}`
              : '语义图谱未启用 · 当前使用确定性文件与模式扫描'
          }
        />
        <InspectionSignal
          warning={snapshot.fileManifest.truncated}
          text={
            snapshot.fileManifest.truncated
              ? '文件索引已达到扫描上限，结果可能不完整'
              : '文件索引完整，未触发扫描上限'
          }
        />
      </footer>
    </section>
  )
}

/** 渲染扫描结果分区标题。 */
function BlockTitle({ icon, title }: { icon: ReactElement; title: string }): ReactElement {
  return (
    <div className={cx('workspace-inspection-block-title')}>
      <span aria-hidden="true">{icon}</span>
      <Text strong>{title}</Text>
    </div>
  )
}

/** 渲染项目根或入口文件列表，并为类型提供中文标签。 */
function PathList({
  emptyText,
  items
}: {
  emptyText: string
  items: WorkspaceInspectionPathItem[]
}): ReactElement {
  if (items.length === 0) return <Text type="secondary">{emptyText}</Text>
  return (
    <ul className={cx('workspace-inspection-paths')}>
      {items.map((item) => (
        <li key={`${item.kind}:${item.path}`}>
          <span>{workspaceKindLabel(item.kind)}</span>
          <code title={item.path}>{item.path}</code>
        </li>
      ))}
    </ul>
  )
}

/** 展示扫描能力或完整性状态。 */
function InspectionSignal({ text, warning }: { text: string; warning: boolean }): ReactElement {
  return (
    <span className={cx('workspace-inspection-signal', warning && 'warning')}>
      {warning ? <WarningOutlined /> : <CheckCircleOutlined />}
      <span>{text}</span>
    </span>
  )
}

/** 将后端稳定类型标识转换为紧凑中文标签。 */
function workspaceKindLabel(kind: string): string {
  const labels: Record<string, string> = {
    backend: '后端',
    frontend: '前端',
    electron_main: '主进程',
    backend_api: 'API',
    workflow_graph: '工作流',
    frontend_renderer: '渲染器',
    frontend_app: '应用',
    frontend_vite: '构建'
  }
  return labels[kind] || kind || '未知'
}

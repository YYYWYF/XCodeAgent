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
  CodeGraphDistribution,
  CodeGraphSymbolPreview,
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
  const graph = snapshot.codeGraph
  const graphStatus = graph.status || (graph.available ? 'ready' : 'unavailable')
  const graphWarning =
    !graph.available || ['failed', 'indexing', 'unavailable'].includes(graphStatus)
  const graphStatusLabel =
    snapshot.cacheHit || graph.cacheHit || graphStatus === 'cache_hit'
      ? 'CACHE HIT'
      : graphStatus === 'incremental' || graph.buildType === 'incremental'
        ? 'INCREMENTAL READY'
        : graphStatus === 'indexing'
          ? 'BACKGROUND INDEXING'
          : graph.available
            ? 'GRAPH READY'
            : 'FILE SEARCH FALLBACK'
  const statusParts = [
    graphStatusLabel,
    graph.providerVersion ? `CRG ${graph.providerVersion}` : undefined,
    graph.buildType && graph.buildType !== 'cache_hit'
      ? graph.buildType.replaceAll('_', ' ').toUpperCase()
      : undefined,
    graph.available && graph.durationMs ? `${(graph.durationMs / 1_000).toFixed(1)}s` : undefined
  ].filter(Boolean)
  const metrics = [
    { label: '工作区文件', value: snapshot.fileManifest.totalFiles },
    { label: '可识别源码', value: snapshot.fileManifest.sourceFiles },
    { label: '图解析文件', value: graph.available ? graph.filesIndexed : undefined },
    { label: '图节点', value: graph.available ? graph.symbolsIndexed : undefined },
    { label: '图关系', value: graph.available ? graph.relationsIndexed : undefined }
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
              工作区代码扫描
            </Text>
          </span>
        </div>
        <div className={cx('workspace-inspection-status')}>
          <span className={cx('workspace-inspection-status-dot')} aria-hidden="true" />
          <span>{statusParts.join(' · ')}</span>
          <code>REV {revision}</code>
        </div>
      </header>

      <div className={cx('workspace-inspection-metrics')}>
        {metrics.map((metric, index) => (
          <div className={cx('workspace-inspection-metric')} key={metric.label}>
            <small>{String(index + 1).padStart(2, '0')}</small>
            <strong>{metric.value === undefined ? '—' : metric.value.toLocaleString()}</strong>
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
          <BlockTitle icon={<RadarChartOutlined />} title="工程入口" />
          <PathList emptyText="未识别到已知入口文件" items={snapshot.entrypoints} />
        </section>
      </div>

      <CodeGraphSummary graph={graph} />

      <footer className={cx('workspace-inspection-signals')}>
        <InspectionSignal
          warning={graphWarning}
          text={
            graph.available
              ? `代码图已就绪 · ${graph.provider}${graph.buildType ? ` · ${graph.buildType}` : ''}`
              : graphStatus === 'indexing'
                ? '代码图仍在后台构建 · 当前使用文件搜索'
                : graphStatus === 'failed'
                  ? '代码图构建失败 · 当前使用文件搜索'
                  : '代码图不可用 · 当前使用确定性文件与模式扫描'
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

/** 展示 CRG 的分类统计、代表性符号和脱敏 warning。 */
function CodeGraphSummary({
  graph
}: {
  graph: WorkspaceInspectionSnapshot['codeGraph']
}): ReactElement {
  const available = graph.available
  const nodes = graph.nodesByKind || []
  const relations = graph.relationsByKind || []
  const samples = graph.sampleSymbols || []
  const warnings = graph.warnings || []
  return (
    <section className={cx('workspace-inspection-accordion')}>
      <details open={available}>
        <summary>
          <span className={cx('workspace-inspection-accordion-title')}>
            <RadarChartOutlined />
            <Text strong>代码图摘要</Text>
          </span>
          <span className={cx('workspace-inspection-accordion-meta')}>
            {available ? `${nodes.length + relations.length} 类统计` : '文件搜索降级'}
          </span>
        </summary>
        {available ? (
          <div className={cx('workspace-inspection-graph-summary')}>
            <div className={cx('workspace-inspection-summary-column')}>
              <SummaryHeading title="语言" />
              <div className={cx('workspace-inspection-tags')}>
                {(graph.languages || []).length > 0 ? (
                  (graph.languages || []).map((language) => <span key={language}>{language}</span>)
                ) : (
                  <Text type="secondary">未返回语言统计</Text>
                )}
              </div>
              <SummaryHeading title="节点构成" />
              <DistributionList items={nodes} />
            </div>
            <div className={cx('workspace-inspection-summary-column')}>
              <SummaryHeading title="关系构成" />
              <DistributionList items={relations} />
              <SummaryHeading title="代表性符号" />
              <SymbolPreviewList items={samples} />
            </div>
            {graph.warningCount || warnings.length > 0 ? (
              <div className={cx('workspace-inspection-summary-warning')}>
                <WarningOutlined />
                <span>
                  解析 warning {graph.warningCount || warnings.length} 条
                  {warnings.length > 0 ? `：${warnings.join('；')}` : ''}
                </span>
              </div>
            ) : null}
          </div>
        ) : (
          <Text type="secondary">
            {graph.message || '代码图暂不可用，Agent 将继续使用文件搜索。'}
          </Text>
        )}
      </details>
    </section>
  )
}

/** 渲染摘要分区的小标题。 */
function SummaryHeading({ title }: { title: string }): ReactElement {
  return <Text className={cx('workspace-inspection-summary-heading')}>{title}</Text>
}

/** 渲染节点或关系分类统计。 */
function DistributionList({ items }: { items: CodeGraphDistribution[] }): ReactElement {
  if (items.length === 0) return <Text type="secondary">暂无分类统计</Text>
  return (
    <div className={cx('workspace-inspection-distributions')}>
      {items.map((item) => (
        <span key={item.kind}>
          <code>{item.kind}</code>
          <strong>{item.count.toLocaleString()}</strong>
        </span>
      ))}
    </div>
  )
}

/** 渲染代表性符号的相对路径、类型和行号。 */
function SymbolPreviewList({ items }: { items: CodeGraphSymbolPreview[] }): ReactElement {
  if (items.length === 0) return <Text type="secondary">暂无代表性符号</Text>
  return (
    <ul className={cx('workspace-inspection-symbol-list')}>
      {items.map((item, index) => (
        <li key={`${item.path}:${item.name}:${index}`}>
          <code title={item.path}>{item.path}</code>
          <span>
            {item.name || '未命名符号'} · {item.kind || 'symbol'} · L{item.lineStart}–{item.lineEnd}
          </span>
        </li>
      ))}
    </ul>
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

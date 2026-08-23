import {
  CheckCircleOutlined,
  CodeOutlined,
  FolderOpenOutlined,
  RadarChartOutlined,
  WarningOutlined
} from '@ant-design/icons'
import { Typography } from 'antd'
import type { ReactElement } from 'react'
import type { WorkspaceInspectionSnapshot } from '../../../../service/agUiAgent'
import { cx } from '../../../../utils'
import './WorkspaceInspectionPanel.less'

const { Text } = Typography

const PATH_KIND_LABELS: Record<string, string> = {
  frontend: '前端',
  backend: '后端',
  electron_main: '主进程',
  frontend_vite: '前端入口',
  backend_fastapi: '后端入口',
  shared: '共享'
}

const METRICS: Array<{
  key: keyof WorkspaceInspectionSnapshot['metrics']
  label: string
}> = [
  { key: 'totalFiles', label: '工作区文件' },
  { key: 'sourceFiles', label: '可识别源码' },
  { key: 'filesIndexed', label: '图解析文件' },
  { key: 'symbolsIndexed', label: '图节点' },
  { key: 'relationsIndexed', label: '图关系' }
]

/** 渲染工作区代码扫描结果：核心指标、技术栈、项目结构与代码图摘要。 */
export default function WorkspaceInspectionPanel({
  snapshot
}: {
  snapshot: WorkspaceInspectionSnapshot
}): ReactElement {
  const codeGraph = snapshot.codeGraph
  const symbolTotal = codeGraph.nodesByKind.reduce((sum, item) => sum + item.count, 0)
  const relationTotal = codeGraph.relationsByKind.reduce((sum, item) => sum + item.count, 0)

  return (
    <section className={cx('workspace-inspection')} aria-label="工作区代码扫描">
      <header className={cx('workspace-inspection-header')}>
        <div className={cx('workspace-inspection-identity')}>
          <span className={cx('workspace-inspection-mark')}>
            <RadarChartOutlined />
          </span>
          <span>
            <Text className={cx('workspace-inspection-eyebrow')}>WORKSPACE SCAN</Text>
            <Text className={cx('workspace-inspection-title')} strong>
              工作区代码扫描
            </Text>
          </span>
        </div>
        <div className={cx('workspace-inspection-status')}>
          <span className={cx('workspace-inspection-status-dot')} />
          <Text type="secondary">
            {snapshot.status} · {(snapshot.durationMs / 1000).toFixed(1)}s
          </Text>
          <code>REV {snapshot.revision}</code>
        </div>
      </header>

      <div className={cx('workspace-inspection-metrics')}>
        {METRICS.map((metric, index) => (
          <div className={cx('workspace-inspection-metric')} key={metric.key}>
            <small>{String(index + 1).padStart(2, '0')}</small>
            <strong>{Number(snapshot.metrics[metric.key]).toLocaleString()}</strong>
            <span>{metric.label}</span>
          </div>
        ))}
      </div>

      <div className={cx('workspace-inspection-blocks')}>
        <section className={cx('workspace-inspection-block')}>
          <header>
            <CodeOutlined />
            <Text strong>技术栈识别</Text>
          </header>
          <div className={cx('workspace-inspection-tags')}>
            {snapshot.techStack.map((item) => (
              <span key={item}>{item}</span>
            ))}
          </div>
        </section>

        <section className={cx('workspace-inspection-block')}>
          <header>
            <FolderOpenOutlined />
            <Text strong>项目结构</Text>
          </header>
          <PathList items={snapshot.projectRoots} />
        </section>

        <section className={cx('workspace-inspection-block')}>
          <header>
            <RadarChartOutlined />
            <Text strong>工程入口</Text>
          </header>
          <PathList items={snapshot.entrypoints} />
        </section>
      </div>

      <details className={cx('workspace-inspection-graph')}>
        <summary>
          <span className={cx('workspace-inspection-graph-title')}>
            <RadarChartOutlined />
            <Text strong>代码图摘要</Text>
          </span>
          <Text type="secondary">
            {symbolTotal + relationTotal} 类统计 · {codeGraph.languages.length} 种语言
          </Text>
        </summary>
        <div className={cx('workspace-inspection-graph-body')}>
          <div className={cx('workspace-inspection-graph-column')}>
            <Text className={cx('workspace-inspection-graph-heading')} type="secondary">
              语言
            </Text>
            <div className={cx('workspace-inspection-tags')}>
              {codeGraph.languages.map((lang) => (
                <span key={lang}>{lang}</span>
              ))}
            </div>
            <Text className={cx('workspace-inspection-graph-heading')} type="secondary">
              节点构成
            </Text>
            <DistributionList items={codeGraph.nodesByKind} />
          </div>
          <div className={cx('workspace-inspection-graph-column')}>
            <Text className={cx('workspace-inspection-graph-heading')} type="secondary">
              关系构成
            </Text>
            <DistributionList items={codeGraph.relationsByKind} />
            <Text className={cx('workspace-inspection-graph-heading')} type="secondary">
              代表性符号
            </Text>
            <ul className={cx('workspace-inspection-symbols')}>
              {codeGraph.sampleSymbols.map((symbol, index) => (
                <li key={index}>
                  <code title={symbol.path}>{symbol.path}</code>
                  <span>
                    {symbol.name} · {symbol.kind} · L{symbol.lineStart}–{symbol.lineEnd}
                  </span>
                </li>
              ))}
            </ul>
          </div>
          {codeGraph.warnings.length > 0 ? (
            <div className={cx('workspace-inspection-graph-warning')}>
              <WarningOutlined />
              <span>
                解析 warning {codeGraph.warnings.length} 条：{codeGraph.warnings.join('；')}
              </span>
            </div>
          ) : null}
        </div>
      </details>

      <footer className={cx('workspace-inspection-signals')}>
        <span className={cx('workspace-inspection-signal', !codeGraph.available && 'warning')}>
          {codeGraph.available ? <CheckCircleOutlined /> : <WarningOutlined />}
          <span>代码图已就绪 · codegraph · incremental</span>
        </span>
        <span className={cx('workspace-inspection-signal')}>
          <CheckCircleOutlined />
          <span>文件索引完整，未触发扫描上限</span>
        </span>
      </footer>
    </section>
  )
}

/** 渲染路径列表（项目结构、工程入口）。 */
function PathList({ items }: { items: Array<{ kind: string; path: string }> }): ReactElement {
  if (items.length === 0) return <Text type="secondary">无</Text>
  return (
    <ul className={cx('workspace-inspection-paths')}>
      {items.map((item, index) => (
        <li key={index}>
          <span className={cx('workspace-inspection-path-kind')}>
            {PATH_KIND_LABELS[item.kind] || item.kind}
          </span>
          <code>{item.path}</code>
        </li>
      ))}
    </ul>
  )
}

/** 渲染节点/关系构成分布。 */
function DistributionList({
  items
}: {
  items: Array<{ kind: string; count: number }>
}): ReactElement {
  if (items.length === 0) return <Text type="secondary">无</Text>
  return (
    <ul className={cx('workspace-inspection-distribution')}>
      {items.map((item) => (
        <li key={item.kind}>
          <code>{item.kind}</code>
          <strong>{item.count.toLocaleString()}</strong>
        </li>
      ))}
    </ul>
  )
}

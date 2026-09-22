import { ApiOutlined, DatabaseOutlined } from '@ant-design/icons'
import {
  type BindableTarget,
  type ExternalApiDomain,
  type ExternalApiSource
} from '../../../DataSources/catalog'
import { cx } from '../../../../utils'
import type { MappingSourceTreeNode } from './types'

/** 来源目录索引的最小切片：树构建只消费目标清单、域清单与服务清单。 */
type CatalogIndex = {
  targets: BindableTarget[]
  externalDomains: ExternalApiDomain[]
  externalServices: ExternalApiSource[]
}

/**
 * 直连绑定三级来源树：类型（数据表/外部接口）→ 连接/域 → 表/接口，叶子 value 为绑定目标键。
 * 与数据来源抽屉的目录层级、工作流「选类型 → 选来源」的拆步保持同一套结构，来源多时可搜索定位。
 * title 用富样式节点分层排印（分组标题/连接名/叶子名称与说明），搜索过滤走 filterTitle 纯文本。
 * 纯函数：从 AiChatPanel 抽出，便于独立理解与复用。
 */
export function buildFieldMappingSourceTree(index: CatalogIndex): MappingSourceTreeNode[] {
  // 数据表分支：库表目标按连接（sourceId）分组，组名即连接显示名。
  const dbTargets = index.targets.filter((target) => target.sourceKind === 'database')
  const byConnection = new Map<string, BindableTarget[]>()
  dbTargets.forEach((target) => {
    byConnection.set(target.sourceId, [...(byConnection.get(target.sourceId) || []), target])
  })
  // 外部接口分支：接口目标按所属域分组，组名取域显示名（接口 sourceId → 服务 → domainId）。
  const domainNameById = new Map(index.externalDomains.map((domain) => [domain.id, domain.name]))
  const serviceDomainById = new Map(
    index.externalServices.map((service) => [service.id, service.domainId])
  )
  const extTargets = index.targets.filter((target) => target.sourceKind === 'external_service')
  const byDomain = new Map<string, BindableTarget[]>()
  extTargets.forEach((target) => {
    const domainId = serviceDomainById.get(target.sourceId) || 'ungrouped'
    byDomain.set(domainId, [...(byDomain.get(domainId) || []), target])
  })
  return [
    {
      title: (
        <span className={cx('source-tree-group')}>
          <DatabaseOutlined aria-hidden="true" />
          数据表
        </span>
      ),
      filterTitle: '数据表',
      value: 'type:database',
      selectable: false,
      children: [...byConnection.values()].map((list) => ({
        title: <span className={cx('source-tree-conn')}>{list[0].sourceName}</span>,
        filterTitle: list[0].sourceName,
        value: `conn:${list[0].sourceId}`,
        selectable: false,
        children: list.map((target) => ({
          title: (
            <span className={cx('source-tree-leaf')}>
              <strong>{target.targetName}</strong>
              {target.targetComment ? <em>（{target.targetComment}）</em> : null}
            </span>
          ),
          filterTitle: `${target.targetName}（${target.targetComment}）`,
          value: target.key
        }))
      }))
    },
    {
      title: (
        <span className={cx('source-tree-group')}>
          <ApiOutlined aria-hidden="true" />
          外部接口
        </span>
      ),
      filterTitle: '外部接口',
      value: 'type:external',
      selectable: false,
      children: [...byDomain.entries()].map(([domainId, list]) => ({
        title: (
          <span className={cx('source-tree-conn')}>{domainNameById.get(domainId) || '未分组接口'}</span>
        ),
        filterTitle: domainNameById.get(domainId) || '未分组接口',
        value: `domain:${domainId}`,
        selectable: false,
        children: list.map((target) => ({
          title: (
            <span className={cx('source-tree-leaf')}>
              <strong>{target.sourceName}</strong>
              {target.targetName ? <em>（{target.targetName}）</em> : null}
            </span>
          ),
          filterTitle: `${target.sourceName}（${target.targetName}）`,
          value: target.key
        }))
      }))
    }
  ]
}

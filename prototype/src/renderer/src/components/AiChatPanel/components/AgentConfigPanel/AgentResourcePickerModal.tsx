import { DoubleRightOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { Button, Input, Modal, Select, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useMemo, useState } from 'react'
import { cx } from '../../../../utils'
import { AGENT_CONFIG_RESOURCE_KIND_LABELS, filterAgentConfigResources } from './catalog'
import type { AgentConfigResource, AgentConfigResourceKind } from './types'

const { Text } = Typography

type Props = {
  kind: AgentConfigResourceKind
  open: boolean
  resources: AgentConfigResource[]
  selectedIds: string[]
  onAdd: (resource: AgentConfigResource) => void
  onClose: () => void
}

/** 渲染技能、知识检索和工具共用的资源添加弹窗。 */
export default function AgentResourcePickerModal({
  kind,
  open,
  resources,
  selectedIds,
  onAdd,
  onClose
}: Props): ReactElement {
  const [query, setQuery] = useState('')
  const selectedIdSet = useMemo(() => new Set(selectedIds), [selectedIds])
  const filteredResources = useMemo(
    () => filterAgentConfigResources(resources, query),
    [query, resources]
  )

  /** 每次打开弹窗时清理上次的搜索条件，保证从完整目录开始选择。 */
  useEffect(() => {
    if (!open) return
    setQuery('')
  }, [open, kind])

  /** 刷新当前目录的展示条件；原型阶段目录数据来自当前配置快照。 */
  const handleRefresh = (): void => {
    setQuery('')
  }

  return (
    <Modal
      centered
      className={cx('agent-resource-modal')}
      destroyOnClose
      footer={null}
      onCancel={onClose}
      open={open}
      title={`添加${AGENT_CONFIG_RESOURCE_KIND_LABELS[kind]}`}
      width={760}
    >
      <div className={cx('agent-resource-dialog')}>
        <div className={cx('agent-resource-toolbar')}>
          <span className={cx('agent-resource-filter-label')}>所属：</span>
          <Select
            aria-label="资源所属"
            className={cx('agent-resource-owner-select')}
            options={[{ label: '全部', value: 'all' }]}
            value="all"
          />
          <Input
            aria-label={`搜索${AGENT_CONFIG_RESOURCE_KIND_LABELS[kind]}名称`}
            className={cx('agent-resource-search')}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={`搜索${AGENT_CONFIG_RESOURCE_KIND_LABELS[kind]}名称`}
            suffix={<SearchOutlined />}
            value={query}
          />
          <Button
            aria-label="刷新资源列表"
            icon={<ReloadOutlined />}
            onClick={handleRefresh}
            title="刷新"
          />
        </div>

        <div className={cx('agent-resource-table')} role="table">
          <div className={cx('agent-resource-table-head')} role="row">
            <span role="columnheader">名称</span>
            <span role="columnheader">操作</span>
          </div>
          <div className={cx('agent-resource-table-body')} role="rowgroup">
            {filteredResources.length > 0 ? (
              filteredResources.map((resource) => {
                const selected = selectedIdSet.has(resource.id)
                return (
                  <div className={cx('agent-resource-row')} key={resource.id} role="row">
                    <div className={cx('agent-resource-copy')} role="cell">
                      <Text strong>{resource.name}</Text>
                      <Text className={cx('agent-resource-description')} type="secondary">
                        {resource.description}
                      </Text>
                    </div>
                    <Button
                      className={cx('agent-resource-add')}
                      disabled={selected}
                      onClick={() => onAdd(resource)}
                      type="link"
                    >
                      {selected ? '已添加' : '添加'}
                    </Button>
                  </div>
                )
              })
            ) : (
              <div className={cx('agent-resource-empty')} role="status">
                {query ? '没有匹配的资源' : '暂无可添加资源'}
              </div>
            )}
          </div>
        </div>

        <Button
          className={cx('agent-resource-more')}
          icon={<DoubleRightOutlined />}
          onClick={onClose}
          type="link"
        >
          更多技能
        </Button>
      </div>
    </Modal>
  )
}

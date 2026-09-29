import { DownOutlined, UpOutlined } from '@ant-design/icons'
import { Button, Input } from 'antd'
import { useEffect, useState, type ReactElement } from 'react'
import type { WorkflowApiDesignDraft } from '../../../../typings'

type Props = { draft: WorkflowApiDesignDraft; disabled: boolean; readOnly: boolean; onChange: (draft: WorkflowApiDesignDraft) => void }

/** 编辑可选的接口级业务逻辑说明，与当前映射一同暂存和确认。 */
export default function MappingDescription({ draft, disabled, readOnly, onChange }: Props): ReactElement | null {
  const [collapsed, setCollapsed] = useState(!readOnly)
  // 详情默认展示说明，返回编辑模式时默认收起。
  useEffect(() => { setCollapsed(!readOnly) }, [readOnly])
  // 详情中没有实际说明时隐藏整个可选卡片；编辑态仍保留填写入口。
  if (readOnly && !draft.implementationDescription?.trim()) return null
  return <section className={`binding-section field-mapping-description${collapsed ? ' is-collapsed' : ''}`} aria-label="接口映射说明">
    <div className="database-section-heading">
      <div className="database-section-title is-card"><h4>接口映射说明</h4><span className="field-mapping-description-optional">非必填</span></div>
      <Button className="database-section-toggle" type="text" aria-expanded={!collapsed} aria-label={`${collapsed ? '展开' : '收起'}接口映射说明`}
        icon={collapsed ? <DownOutlined /> : <UpOutlined />} onClick={() => setCollapsed((current) => !current)} />
    </div>
    {!collapsed ? readOnly ? <p className="field-mapping-description-text">{draft.implementationDescription}</p> : <>
      <p>用自然语言补充该接口的整体业务逻辑，例如参数校验、执行顺序、业务分支和返回结果处理。</p>
      <Input.TextArea aria-label="接口映射说明" disabled={disabled} maxLength={4000} showCount autoSize={{ minRows: 4, maxRows: 10 }}
        placeholder="例如：先校验当前用户是否有权查看商品，再根据 productId 查询；商品已下架时不返回库存信息。"
        value={draft.implementationDescription || ''} onChange={(event) => onChange({ ...draft, implementationDescription: event.target.value })} />
    </> : null}
  </section>
}

import { ApiOutlined, DatabaseOutlined, NodeIndexOutlined, SettingOutlined } from '@ant-design/icons'
import { Button, Modal } from 'antd'
import { useState } from 'react'
import type { ReactElement } from 'react'

export type BusinessRuleInput = { source: 'database' | 'external_api' | 'endpoint' | 'builtin'; name: string; description?: string; type?: string; dataSource?: string; parameterLocation?: string }
type Props = { title: string; configured: boolean; inputCount: number; disabled: boolean; inline?: boolean; onClick?: () => void; description?: string; inputs?: BusinessRuleInput[] }

/** 以单个紧凑入口承载规则状态，详细输入和处理逻辑留在规则弹窗中。 */
export default function BusinessRuleEntry({ title, configured, inputCount, disabled, inline = false, onClick, description, inputs = [] }: Props): ReactElement {
  const [viewing, setViewing] = useState(false)
  const readOnly = !onClick
  return <>
    <Button type={inline ? 'link' : 'default'} className={inline ? 'field-business-inline-link' : 'field-business-entry'} aria-label={`${title}${readOnly ? '查看规则' : configured ? '编辑规则' : '配置规则'}`} disabled={disabled}
      onClick={onClick || (() => setViewing(true))}>
      <span className="field-business-entry-label">{!inline ? <NodeIndexOutlined aria-hidden="true" /> : null}{readOnly ? '查看规则' : configured ? '已配置规则' : '配置业务规则'}</span>
      {!inline && inputCount > 0 ? <span className="field-business-entry-count">{inputCount} 个输入</span> : null}
    </Button>
    {readOnly ? <Modal className="field-rule-modal field-business-viewer" width={920} centered title={<span className="field-business-viewer-title"><NodeIndexOutlined aria-hidden="true" />业务规则 · {title}</span>} open={viewing}
      footer={<Button type="primary" onClick={() => setViewing(false)}>关闭</Button>} onCancel={() => setViewing(false)}>
      <section className="field-business-viewer-section">
        <h3>处理输入 <span>{inputs.length} 项</span></h3>
        {inputs.length ? <div className="field-business-viewer-table-wrap"><table className="field-business-viewer-table">
          <thead><tr><th scope="col">值来源</th><th scope="col">字段</th><th scope="col">类型</th><th scope="col">所属数据源</th><th scope="col">参数归属</th></tr></thead>
          <tbody>{inputs.map((input, index) => <tr key={`${index}:${input.name}`}>
            <td><span className="field-business-viewer-source">{input.source === 'database' ? <DatabaseOutlined /> : input.source === 'builtin' ? <SettingOutlined /> : <ApiOutlined />}{input.source === 'endpoint' ? '接口参数' : input.source === 'builtin' ? '内置参数' : '数据源字段'}</span></td>
            <td><code>{input.name}</code>{input.description ? <span className="field-business-viewer-description">{input.description}</span> : null}</td>
            <td>{input.type || '-'}</td><td>{input.dataSource?.trim() || '-'}</td><td>{input.parameterLocation || '-'}</td>
          </tr>)}</tbody>
        </table></div> : <div className="field-business-viewer-empty">无需输入字段</div>}
      </section>
      <section className="field-business-viewer-section"><h3>处理逻辑</h3><p className="field-business-viewer-logic">{description || '尚未填写处理逻辑'}</p></section>
    </Modal> : null}
  </>
}

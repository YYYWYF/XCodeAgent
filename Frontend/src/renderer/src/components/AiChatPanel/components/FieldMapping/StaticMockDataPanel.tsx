import { Alert, Button, Input, InputNumber, Spin, Tag } from 'antd'
import { BulbOutlined } from '@ant-design/icons'
import type { ReactElement } from 'react'
import type { MockDataAssistState } from './useMockDataAssist'
import { MAPPING_REQUIREMENTS_LIMIT } from './mappingAssistModel'
import './MappingAssist.less'
import './StaticMockDataPanel.less'

/** 独立预览虚构样例，显式应用到数据内容后再沿原映射流程确认。 */
export default function StaticMockDataPanel({ assist, disabled }: { assist: MockDataAssistState; disabled: boolean }): ReactElement | null {
  const running = assist.phase !== 'idle'
  const result = assist.result
  return <>
    {assist.receipt && <div className="mapping-assist-receipt" role="status"><div><strong>Mock 数据已应用 · 尚未保存</strong>
      <p>请核对返回映射。继续编辑、暂存、确认或切换接口后，撤销失效。</p></div><Button disabled={disabled || running} onClick={assist.undo}>撤销本次数据替换</Button></div>}
    {assist.open && <section className="mapping-assist-panel static-mock-panel" aria-label="AI 生成 Mock 数据">
      <div className="mapping-assist-heading"><strong><BulbOutlined /> 生成 Mock 数据 <Tag>模拟样例</Tag></strong>
        <Button type="text" disabled={assist.phase === 'applying'} onClick={assist.close}>收起</Button></div>
      <p className="mapping-assist-secondary">根据接口返回契约生成虚构样例，预览后应用到数据内容。</p>
      <label>Mock 数据需求 <span className="mapping-assist-secondary">（必填）</span><Input.TextArea aria-label="Mock 数据需求" required
        value={assist.instruction} maxLength={MAPPING_REQUIREMENTS_LIMIT} autoSize={{ minRows: 2, maxRows: 5 }} disabled={disabled || running}
        placeholder="例如：生成商品列表样例，名称使用中文，覆盖不同的商品类别。" onChange={(event) => assist.setInstruction(event.target.value)} /></label>
      <div className="mapping-assist-toolbar"><label className="static-mock-count">每组列表条数<InputNumber aria-label="Mock 每组列表条数" min={1} max={20} precision={0}
        value={assist.rowCount} disabled={disabled || running} onChange={(value) => assist.setRowCount(Number(value))} /></label>
        <Button type="primary" disabled={disabled || running || !assist.instruction.trim() || assist.instruction.trim().length > MAPPING_REQUIREMENTS_LIMIT} onClick={() => void assist.generate()}>{result ? '重新生成 Mock 数据' : '生成 Mock 数据'}</Button></div>
      {assist.phase === 'generating' && <div className="mapping-assist-toolbar" role="status"><span><Spin size="small" /> {assist.progress?.message || '正在准备样例'}</span><Button onClick={assist.cancel}>取消生成</Button></div>}
      {assist.error && <Alert type="error" showIcon message={assist.error} />}
      {assist.stale && <Alert type="warning" showIcon message="草稿或契约已变化，请重新生成 Mock 数据。" />}
      {assist.requirementsChanged && <Alert type="warning" showIcon message="当前需求或条数尚未用于生成，请重新生成后应用。" />}
      {result && <div className="static-mock-result">
        {result.questions.length > 0 && <Alert type="warning" showIcon message="以下信息需要明确" description={<ul>{result.questions.map((question) => <li key={question}>{question}</li>)}</ul>} />}
        {result.data != null && <><div className="mapping-assist-toolbar"><strong>Mock 预览 <Tag>尚未应用</Tag></strong><span className="mapping-assist-secondary">{result.fields.length} 个字段</span></div>
          <pre aria-label="生成的 Mock 数据预览">{JSON.stringify(result.data, null, 2)}</pre>
          {result.warnings.map((warning) => <Alert key={warning} type="warning" showIcon message={warning} />)}
          <div className="mapping-assist-toolbar"><span className="mapping-assist-secondary">应用将替换数据内容，已有返回映射保留，请核对路径和类型后保存确认。</span>
            <Button type="primary" loading={assist.phase === 'applying'} disabled={disabled || !assist.canApply} onClick={() => void assist.apply()}>应用到数据内容</Button></div></>}
      </div>}
    </section>}
  </>
}

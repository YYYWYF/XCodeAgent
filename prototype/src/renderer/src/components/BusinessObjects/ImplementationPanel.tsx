import { useEffect, useRef, useState } from 'react'
import { Button, Input, Select } from 'antd'
import {
  ApiOutlined,
  CheckCircleOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  ThunderboltOutlined
} from '@ant-design/icons'
import { bindingKey, useDataSourceIndex, type BindableTarget } from '../DataSources/catalog'
import {
  autoMatchFields,
  emptyImplementation,
  fieldCandidates,
  implementationSummary,
  localSourceBinding,
  missingBindingSources,
  type BusinessOperation,
  type Implementation,
  type ImplementationKind
} from './model'

type Props = {
  operation: BusinessOperation
  /** 阶段是否允许配置：计划与开发阶段可编辑，验证类阶段只读。 */
  editable: boolean
  /** intent=计划阶段记录来源级意向；bind=开发阶段完成表/接口级绑定与字段映射。 */
  mode: 'intent' | 'bind'
  onChange: (operation: BusinessOperation) => void
}

const KIND_OPTIONS: ImplementationKind[] = ['数据库', '外部服务', '本地实现', '多来源组合']

/** 配置草稿：来源选择 + 规则说明；两种模式共用，保存时投影回 Implementation。 */
type Draft = {
  kind: ImplementationKind
  selectedKeys: string[]
  intentSources: string[]
  rule: string
}

/** 从当前实现初始化配置草稿；绑定阶段沿用已选目标，意向阶段沿用来源名。 */
function draftFrom(operation: BusinessOperation, mode: 'intent' | 'bind'): Draft {
  const implementation = operation.implementation
  if (mode === 'intent') {
    return {
      kind: implementation.kind,
      selectedKeys: [],
      intentSources: [...implementation.intentSources],
      rule: implementation.rule
    }
  }
  return {
    kind: implementation.kind,
    selectedKeys: implementation.bindings.map((binding) =>
      bindingKey(binding.sourceId, binding.targetName)
    ),
    intentSources: [],
    rule: implementation.rule
  }
}

/** 展示操作级数据实现：计划阶段记录意向，开发阶段完成绑定映射，验证阶段只读。 */
export default function ImplementationPanel({
  operation,
  editable,
  mode,
  onChange
}: Props): JSX.Element {
  const catalog = useDataSourceIndex()
  const [configuring, setConfiguring] = useState(false)
  const [draft, setDraft] = useState<Draft>(() => draftFrom(operation, mode))
  const [running, setRunning] = useState(false)
  const [preview, setPreview] = useState(false)
  const timer = useRef<number>()
  // 切换对象、操作或阶段时取消尚未完成的模拟，避免迟到结果覆盖新草稿。
  useEffect(() => () => window.clearTimeout(timer.current), [])
  // 只在切换操作或模式时复位本地配置态；跨面板同步刷新不打断正在进行的配置。
  useEffect(() => {
    setConfiguring(false)
    setDraft(draftFrom(operation, mode))
    setPreview(false)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [operation.id, mode])
  const implementation = operation.implementation
  const missing = missingBindingSources(implementation, catalog.sources)
  const candidates = fieldCandidates(implementation, catalog.sources)
  const matchedCount = implementation.mappings.filter((item) => item.matched).length
  const pendingMappings = implementation.mappings.filter((item) => !item.matched)
  const allMatched =
    implementation.matched &&
    implementation.mappings.length > 0 &&
    implementation.mappings.every((item) => item.matched)
  const summary = implementationSummary(implementation)

  /** 切换实现类型后重置来源选择，避免保留前一种实现的确认状态。 */
  const changeKind = (kind: ImplementationKind): void => {
    setDraft({ kind, selectedKeys: [], intentSources: [], rule: '' })
  }
  /** 记录来源目标的多选/单选：单来源模式只保留最后一个选择。 */
  const toggleTarget = (key: string): void => {
    setDraft((current) => {
      const selected =
        current.kind === '多来源组合'
          ? current.selectedKeys.includes(key)
            ? current.selectedKeys.filter((item) => item !== key)
            : [...current.selectedKeys, key]
          : [key]
      return { ...current, selectedKeys: selected }
    })
  }
  /** 计划阶段：勾选/取消来源级意向。 */
  const toggleIntentSource = (name: string): void => {
    setDraft((current) => {
      const selected = current.intentSources.includes(name)
        ? current.intentSources.filter((item) => item !== name)
        : current.kind === '多来源组合'
          ? [...current.intentSources, name]
          : [name]
      return { ...current, intentSources: selected }
    })
  }
  /** 配置草稿是否满足保存条件。 */
  const draftReady = (): boolean => {
    if (draft.kind === '本地实现') return true
    if (mode === 'intent') {
      return draft.intentSources.length >= (draft.kind === '多来源组合' ? 2 : 1)
    }
    return draft.selectedKeys.length >= (draft.kind === '多来源组合' ? 2 : 1)
  }
  /** 把草稿投影回实现结构：意向只写来源名；绑定写目标并触发字段匹配。 */
  const saveDraft = (): void => {
    if (mode === 'intent') {
      onChange({
        ...operation,
        implementation: {
          ...emptyImplementation(draft.kind),
          kind: draft.kind,
          intentSources: draft.intentSources,
          rule: draft.rule
        }
      })
      setConfiguring(false)
      return
    }
    const bindings =
      draft.kind === '本地实现'
        ? [{ ...localSourceBinding }]
        : draft.selectedKeys.flatMap((key) => {
            const target = catalog.targetByKey.get(key)
            return target
              ? [
                  {
                    sourceId: target.sourceId,
                    sourceName: target.sourceName,
                    targetName: target.targetName,
                    targetComment: target.targetComment
                  }
                ]
              : []
          })
    // 保存即执行一次模拟 AI 匹配：能确定的自动填，疑似歧义的字段留给人工确认。
    setRunning(true)
    const next: Implementation = {
      ...emptyImplementation(draft.kind),
      kind: draft.kind,
      bindings,
      rule: draft.rule,
      matched: true
    }
    timer.current = window.setTimeout(() => {
      onChange({
        ...operation,
        implementation: {
          ...next,
          mappings: autoMatchFields(operation.outputs, next, catalog.sources)
        }
      })
      setRunning(false)
      setConfiguring(false)
      setPreview(false)
    }, 650)
  }
  /** 人工选定某个字段的来源后立即写入映射，并保持待确认状态直到用户确认绑定。 */
  const chooseMapping = (field: string, label: string): void => {
    onChange({
      ...operation,
      implementation: {
        ...implementation,
        confirmed: false,
        mappings: implementation.mappings.map((item) =>
          item.field === field
            ? { ...item, matched: true, matchType: 'manual', sourceLabel: label }
            : item
        )
      }
    })
  }

  const statusBadge = implementation.confirmed ? (
    <span className="bo-success">
      <CheckCircleOutlined /> 已绑定
    </span>
  ) : mode === 'bind' ? (
    <span
      className={
        implementation.matched && implementation.mappings.length ? 'bo-pending' : 'bo-muted'
      }
    >
      {pendingMappings.length ? `${pendingMappings.length} 个字段待确认` : '待绑定'}
    </span>
  ) : implementation.intentSources.length ? (
    <span className="bo-pending">意向已记录</span>
  ) : (
    <span className="bo-muted">待配置</span>
  )

  /** 渲染来源目标卡片列表：数据库表与外部接口统一为可选卡片。 */
  const renderTargetCards = (targets: BindableTarget[], multiple: boolean): JSX.Element => {
    if (!targets.length) {
      return <p className="bo-muted">该来源暂无可绑定目标，请先在左侧「数据来源」中补全目录。</p>
    }
    return (
      <div className="bo-target-list">
        {targets.map((target) => {
          const key = bindingKey(target.sourceId, target.targetName)
          const selected = draft.selectedKeys.includes(key)
          return (
            <button
              key={key}
              className={selected ? 'selected' : ''}
              onClick={() => toggleTarget(key)}
              type="button"
            >
              {target.sourceKind === 'database' ? <DatabaseOutlined /> : <ApiOutlined />}
              <span className="bo-target-copy">
                <strong>{target.targetName}</strong>
                <small>
                  {target.sourceName} · {target.targetComment} · {target.fields.length} 个字段
                </small>
              </span>
              <em>{multiple ? (selected ? '已选' : '选择') : selected ? '✓' : ''}</em>
            </button>
          )
        })}
      </div>
    )
  }

  /** 配置态中按实现类型渲染来源选择区。 */
  const renderSourcePicker = (): JSX.Element => {
    if (draft.kind === '本地实现') {
      return (
        <label>
          业务规则
          <Input.TextArea
            aria-label="业务规则"
            autoSize={{ minRows: 2, maxRows: 4 }}
            placeholder="描述输入校验、业务规则与返回结果，例如：校验输入 → 执行业务规则 → 返回结果"
            value={draft.rule}
            onChange={(event) => setDraft({ ...draft, rule: event.target.value })}
          />
        </label>
      )
    }
    if (mode === 'intent') {
      const sourceNames =
        draft.kind === '数据库'
          ? catalog.databases.map((item) => item.name)
          : draft.kind === '外部服务'
            ? catalog.externalServices.map((item) => item.name)
            : [
                ...catalog.databases.map((item) => item.name),
                ...catalog.externalServices.map((item) => item.name)
              ]
      return (
        <div className="bo-intent-sources">
          {sourceNames.map((name) => (
            <button
              key={name}
              className={draft.intentSources.includes(name) ? 'selected' : ''}
              onClick={() => toggleIntentSource(name)}
              type="button"
            >
              {name}
              {draft.intentSources.includes(name) && <em>✓</em>}
            </button>
          ))}
          {!sourceNames.length && (
            <p className="bo-muted">目录为空，请先在左侧「数据来源」中登记来源。</p>
          )}
        </div>
      )
    }
    const allowedKinds: Array<'database' | 'external_service'> =
      draft.kind === '数据库'
        ? ['database']
        : draft.kind === '外部服务'
          ? ['external_service']
          : ['database', 'external_service']
    const targets = catalog.targets.filter((target) => allowedKinds.includes(target.sourceKind))
    return renderTargetCards(targets, draft.kind === '多来源组合')
  }

  return (
    <section className="bo-implementation">
      <div className="bo-section-heading">
        <h3>{mode === 'bind' ? '数据绑定' : '数据实现意向'}</h3>
        {statusBadge}
      </div>
      <p className="bo-muted">
        {mode === 'bind'
          ? '为这项操作选择数据库表或外部服务接口，AI 自动推导字段关系，你只需确认结果。'
          : '在计划阶段标注这项操作拟使用的数据来源；表级/接口级绑定与字段映射将在开发阶段完成。'}
      </p>
      {configuring ? (
        <div className="bo-config">
          <div className="bo-kind-grid">
            {KIND_OPTIONS.map((kind) => (
              <button
                key={kind}
                className={draft.kind === kind ? 'selected' : ''}
                onClick={() => changeKind(kind)}
              >
                {kind}
              </button>
            ))}
          </div>
          <label>
            {draft.kind === '本地实现'
              ? '实现说明'
              : mode === 'intent'
                ? '拟使用的来源'
                : '选择绑定目标'}
          </label>
          {renderSourcePicker()}
          {draft.kind === '多来源组合' && (
            <label>
              组合规则
              <Input.TextArea
                aria-label="组合规则"
                autoSize={{ minRows: 2, maxRows: 4 }}
                placeholder="描述各来源如何关联与汇总，例如：按业务编号关联、调用服务并汇总为本操作的返回结果"
                value={draft.rule}
                onChange={(event) => setDraft({ ...draft, rule: event.target.value })}
              />
            </label>
          )}
          <div className="bo-actions">
            <Button disabled={running} onClick={() => setConfiguring(false)}>
              取消
            </Button>
            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              loading={running}
              disabled={!draftReady()}
              onClick={saveDraft}
            >
              {mode === 'intent' ? '保存意向' : '生成字段映射'}
            </Button>
          </div>
        </div>
      ) : (
        <>
          {missing.length > 0 && (
            <div className="bo-exception">
              <strong>绑定来源已从目录移除</strong>
              <p>{missing.join('、')} 不在当前数据来源目录中，请重新绑定本操作。</p>
            </div>
          )}
          <div className="bo-source-card">
            <span className="bo-eyebrow">{implementation.kind}</span>
            {mode === 'bind' && implementation.bindings.length ? (
              <ul className="bo-binding-list">
                {implementation.bindings.map((binding) => (
                  <li key={`${binding.sourceId}-${binding.targetName}`}>
                    <strong>
                      {binding.sourceId === 'local'
                        ? binding.sourceName
                        : `${binding.sourceName} · ${binding.targetName}`}
                    </strong>
                    <small>{binding.targetComment}</small>
                  </li>
                ))}
              </ul>
            ) : (
              <strong>{summary || '尚未选择数据实现'}</strong>
            )}
            <p>
              {mode === 'bind'
                ? implementation.kind === '多来源组合' && implementation.rule
                  ? implementation.rule
                  : implementation.kind === '本地实现' && implementation.rule
                    ? implementation.rule
                    : '页面不直接连接来源；本操作的数据能力由以上绑定提供。'
                : implementation.intentSources.length
                  ? '意向在技术规划方案中记录，开发阶段将完成表级/接口级绑定与字段映射。'
                  : '尚未记录意向，可先标注拟使用的来源类型。'}
            </p>
            {editable && (
              <Button
                size="small"
                onClick={() => {
                  setDraft(draftFrom(operation, mode))
                  setConfiguring(true)
                }}
              >
                {mode === 'bind'
                  ? implementation.bindings.length
                    ? '更换绑定'
                    : '配置绑定'
                  : implementation.intentSources.length
                    ? '调整意向'
                    : '记录意向'}
              </Button>
            )}
          </div>
          {mode === 'bind' && implementation.bindings.length > 0 && (
            <>
              <div className="bo-match-heading">
                <ThunderboltOutlined />
                <strong>
                  已匹配 {matchedCount} / {operation.outputs.length} 个字段
                </strong>
              </div>
              <div className="bo-progress">
                <i
                  style={{
                    width: `${operation.outputs.length ? (matchedCount / operation.outputs.length) * 100 : 0}%`
                  }}
                />
              </div>
              {pendingMappings.length > 0 && (
                <div className="bo-exception">
                  <strong>有 {pendingMappings.length} 个字段需要你确认</strong>
                  <p>这些字段存在多个疑似来源，请为每个返回字段选择业务含义一致的字段。</p>
                </div>
              )}
              <div className="bo-mapping">
                {operation.outputs.map((field) => {
                  const mapping = implementation.mappings.find((item) => item.field === field)
                  return (
                    <div key={field} className={mapping && !mapping.matched ? 'pending' : ''}>
                      <span>{field}</span>
                      {editable && candidates.length ? (
                        <Select
                          aria-label={`选择 ${field} 的来源字段`}
                          placeholder="选择来源字段"
                          size="small"
                          style={{ width: '100%' }}
                          value={mapping?.matched ? mapping.sourceLabel : undefined}
                          onChange={(value) => chooseMapping(field, value)}
                          options={candidates.map((candidate) => ({
                            value: candidate.label,
                            label: candidate.label
                          }))}
                        />
                      ) : (
                        <span>
                          ←{' '}
                          {mapping?.matched
                            ? mapping.sourceLabel
                            : implementation.kind === '本地实现'
                              ? `业务规则 · ${field}`
                              : '尚未匹配'}
                        </span>
                      )}
                    </div>
                  )
                })}
              </div>
              <small className="bo-muted">
                输入：{operation.inputs.join('、') || '无需输入'} → 自动传入操作的对应参数。
              </small>
            </>
          )}
          <div className="bo-actions">
            <Button
              icon={<ExperimentOutlined />}
              disabled={!implementation.confirmed}
              onClick={() => setPreview(!preview)}
            >
              {preview ? '收起结果' : '预览示例结果'}
            </Button>
            {editable && mode === 'bind' && !implementation.confirmed && (
              <Button
                type="primary"
                disabled={!allMatched || missing.length > 0}
                onClick={() =>
                  onChange({
                    ...operation,
                    implementation: { ...implementation, confirmed: true }
                  })
                }
              >
                确认绑定
              </Button>
            )}
          </div>
          {preview && (
            <div className="bo-preview">
              <strong>示例返回结果</strong>
              <p className="bo-muted">静态演示，未发起真实查询或写入。</p>
              {operation.outputs.map((field, index) => (
                <div key={field}>
                  <span>{field}</span>
                  <strong>
                    {field.includes('时间')
                      ? '2026-09-10 09:30'
                      : field.includes('状态')
                        ? '待审核'
                        : field.includes('编号')
                          ? '20260910001'
                          : field.includes('金额') || field.includes('余额')
                            ? '12,800.00'
                            : field.includes('人') || field.includes('姓名')
                              ? '张明（示例）'
                              : `示例值 ${index + 1}`}
                  </strong>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  )
}

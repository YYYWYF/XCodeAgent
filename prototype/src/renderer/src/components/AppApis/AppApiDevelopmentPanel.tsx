import { ApiOutlined, CheckCircleOutlined, LoadingOutlined, SendOutlined } from '@ant-design/icons'
import { Button, Empty, Input } from 'antd'
import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../utils'
import { useDataSourceIndex } from '../DataSources/catalog'
import RichLoading from '../AiChatPanel/components/DesignProgress/RichLoading'
import { contractFieldLabel, contractRequestParams, missingBindingSources } from './model'
import { useAppApis } from './store'
import './AppApis.less'
import './AppApiDevelopmentPanel.less'

/** 按出参语义生成示例值：时间/状态/编号/金额/人名各有拟真口径，其余按序号兜底。 */
function sampleValue(field: string, index: number): string {
  if (field.includes('时间')) return '2026-09-10 09:30'
  if (field.includes('状态')) return '待审核'
  if (field.includes('编号') || field.includes('单号')) return '20260910001'
  if (field.includes('金额') || field.includes('余额')) return '12,800.00'
  if (field.includes('部门')) return '信息技术部'
  if (field.includes('人') || field.includes('姓名')) return '张明'
  return `示例值 ${index + 1}`
}

/**
 * 应用API产物预览（POSTMAN 语义）：第 1 块是「接口调试」——填参发送验证请求，是产物验收的
 * 主操作；第 2 块是「接口定义」——已实现的基本信息（请求、用途、调用页面、出入参、数据来源），
 * 定义行平铺不套灰底盒。头部徽标配套工作流状态（已完成 / 生成中 / 绑定进行中）；
 * 适配生成期间整页给富加载。未确认的绑定可直接在这里发起：进入字段映射工作台
 * 完成来源选定与连线配置，草稿自动保存，不依赖对话区工作流节点。
 */
export default function AppApiDevelopmentPanel({
  objectId,
  requirementSpec,
  versionKey,
  technicalPlan,
  generating = false,
  onOpenFieldMapping
}: {
  objectId: string
  requirementSpec: Record<string, unknown>
  /** 应用API绑定缓存所属版本；与产物目录、开发剧本共用同一把版本键。 */
  versionKey?: string
  /** 技术规划方案：为每个接口提供计划阶段确定的数据实现意向。 */
  technicalPlan?: Record<string, unknown>
  /** 该接口的适配生成进行中（来自当前工作流状态）。 */
  generating?: boolean
  /** 直连绑定入口：进入字段映射工作台继续/发起该接口的绑定；只读版本不提供。 */
  onOpenFieldMapping?: (objectId: string) => void
}): ReactElement {
  const [objects] = useAppApis(requirementSpec, versionKey, technicalPlan)
  const catalog = useDataSourceIndex()
  const object = objects.find((item) => item.id === objectId) || objects[0]
  const implementation = object?.implementation
  const [paramValues, setParamValues] = useState<Record<string, string>>({})
  const [sending, setSending] = useState(false)
  const [responded, setResponded] = useState(false)
  const timer = useRef<number>()
  useEffect(() => () => window.clearTimeout(timer.current), [])
  // 切换接口时复位调试态：响应与试填参数不跨接口残留。
  useEffect(() => {
    setParamValues({})
    setResponded(false)
  }, [object?.id])

  if (!object || !implementation) {
    return <Empty description="未找到应用API" image={Empty.PRESENTED_IMAGE_SIMPLE} />
  }

  const missing = missingBindingSources(implementation, catalog.sources)
  const requestParams = contractRequestParams(object)

  /** 模拟发送：静态演示，未发起真实请求；短延时后给出示例响应。 */
  const send = (): void => {
    setSending(true)
    setResponded(false)
    timer.current = window.setTimeout(() => {
      setSending(false)
      setResponded(true)
    }, 500)
  }

  /** 头部工作流状态徽标：让产物视图与生命周期对齐（已完成 / 生成中 / 绑定进行中）。 */
  const stateBadge = implementation.confirmed ? (
    <span className={cx('bod-state', 'is-delivered')}>
      <CheckCircleOutlined /> 已完成
    </span>
  ) : generating ? (
    <span className={cx('bod-state', 'is-running')}>
      <LoadingOutlined spin /> 生成中
    </span>
  ) : (
    <span className={cx('bod-state')}>绑定进行中</span>
  )

  // 绑定尚未确认：生成中给富加载页，否则只提示绑定在工作流内进行。
  if (!implementation.confirmed) {
    return (
      <section className={cx('app-api-development')}>
        <header className={cx('bod-header')}>
          <div className={cx('bod-title')}>
            <span>
              <ApiOutlined />
            </span>
            <h2>{object.name}</h2>
          </div>
          {stateBadge}
        </header>
        <div className={cx('bod-content')}>
          {missing.length > 0 && (
            <div className={cx('api-terminal-warning')}>
              绑定来源 {missing.join('、')} 已从数据来源目录移除，请在对话区重新发起该应用API的开发。
            </div>
          )}
          {generating ? (
            <div className={cx('api-terminal-placeholder', 'is-generating')}>
              <RichLoading
                title="正在生成数据适配逻辑"
                hint="已确认的映射配置正在生成交付文件，完成后可在此调试验收。"
              />
            </div>
          ) : (
            <div className={cx('api-terminal-placeholder')}>
              <strong>数据绑定待完成</strong>
              <span>
                可以直接在这里发起绑定：进入字段映射工作台完成来源选定、连线与表达式配置，草稿自动保存，中断后随时可继续。
              </span>
              {/* 直连入口：已选定来源为「继续」，未选定为先选来源；来源已从目录移除时只保留警示。 */}
              {missing.length === 0 && onOpenFieldMapping && (
                <Button
                  type="primary"
                  onClick={() => onOpenFieldMapping(object.id)}
                >
                  {implementation.bindings.length ? '继续字段映射' : '绑定数据来源'}
                </Button>
              )}
            </div>
          )}
        </div>
      </section>
    )
  }

  const binding = implementation.bindings[0]
  const templateText =
    implementation.kind === '外部服务'
      ? '参数适配'
      : implementation.kind === '本地实现'
        ? '本地业务规则'
        : `${implementation.tableOp || '查询'}模板`
  const sourceText = binding
    ? `${binding.sourceName} · ${binding.targetName}`
    : implementation.rule || '业务规则'
  const sentParams = requestParams.map((param) => ({
    ...param,
    value: paramValues[param.name] ?? (param.required ? sampleValue(param.summary || param.name, 0) : '')
  }))

  return (
    <section className={cx('app-api-development')}>
      <header className={cx('bod-header')}>
        <div className={cx('bod-title')}>
          <span>
            <ApiOutlined />
          </span>
          <h2>{object.name}</h2>
        </div>
        {stateBadge}
      </header>

      <div className={cx('bod-content')}>
        {missing.length > 0 && (
          <div className={cx('api-terminal-warning')}>
            绑定来源 {missing.join('、')} 已从数据来源目录移除，请在对话区重新发起该应用API的开发。
          </div>
        )}
        {/* 1) 接口调试：请求验证，产物验收的主操作（POSTMAN 第一屏）。 */}
        <section className={cx('api-terminal-section', 'debug-block')}>
          <div className={cx('api-terminal-section-title')}>接口调试</div>
          <div className={cx('api-terminal-urlbar')}>
            <code className={cx('api-terminal-method')}>{object.method}</code>
            <span className={cx('api-terminal-path')}>{object.path}</span>
            <Button
              type="primary"
              icon={<SendOutlined />}
              size="small"
              loading={sending}
              onClick={send}
            >
              发送
            </Button>
          </div>
          {requestParams.length > 0 && (
            <div className={cx('api-terminal-params')}>
              <div className={cx('api-terminal-params-title')}>入参试填</div>
              {sentParams.map((param) => (
                <label key={param.name} className={cx('api-terminal-param')}>
                  <code>{contractFieldLabel(param.code, param.name)}</code>
                  <Input
                    aria-label={`${param.name} 试填值`}
                    onChange={(event) =>
                      setParamValues((current) => ({ ...current, [param.name]: event.target.value }))
                    }
                    placeholder={param.summary || '试填值'}
                    value={param.value}
                  />
                  {!param.required ? <small>可选</small> : null}
                </label>
              ))}
            </div>
          )}
          {responded && (
            <div className={cx('api-terminal-response')}>
              <div className={cx('api-terminal-response-head')}>
                <span className={cx('api-terminal-status')}>200 OK</span>
                <small>模拟响应 · 12 ms · 静态演示，未发起真实请求</small>
              </div>
              {object.response.map((field, index) => {
                const mapping = implementation.mappings.find((item) => item.field === field.name)
                return (
                  <div key={field.name} className={cx('api-terminal-response-row')}>
                    <span>{contractFieldLabel(field.code, field.name)}</span>
                    <strong>{sampleValue(field.name, index)}</strong>
                    <small>
                      {mapping?.matched && implementation.kind !== '本地实现'
                        ? `← ${mapping.sourceLabel}`
                        : '业务规则'}
                    </small>
                  </div>
                )
              })}
            </div>
          )}
          {!responded && !sending && (
            <p className={cx('api-terminal-hint')}>填好入参后点「发送」，查看该应用API的示例返回。</p>
          )}
        </section>
        {/* 2) 接口定义：已实现的基本信息，按语义分行平铺，不再套多层灰底盒子。 */}
        <section className={cx('api-terminal-section')}>
          <div className={cx('api-terminal-section-title')}>接口定义</div>
          <div className={cx('api-definition')}>
            <div className={cx('api-definition-row')}>
              <small>请求</small>
              <span className={cx('api-definition-request')}>
                <code className={cx('api-terminal-method')}>{object.method}</code>
                <code className={cx('api-definition-path')}>{object.path}</code>
              </span>
            </div>
            <div className={cx('api-definition-row')}>
              <small>用途</small>
              <span>{object.description}</span>
            </div>
            <div className={cx('api-definition-row')}>
              <small>调用页面</small>
              <span>{object.pages && object.pages.length ? object.pages.join('、') : '—'}</span>
            </div>
            <div className={cx('api-definition-row')}>
              <small>入参</small>
              <span className={cx('api-definition-io-list')}>
                {requestParams.length
                  ? requestParams.map((param) => (
                      <span key={param.name} className={cx('api-definition-io')}>
                        <code>{contractFieldLabel(param.code, param.name)}</code>
                        {param.required ? null : <em>可选</em>}
                        {param.summary ? <small>{param.summary}</small> : null}
                      </span>
                    ))
                  : '无'}
              </span>
            </div>
            <div className={cx('api-definition-row')}>
              <small>出参</small>
              <span className={cx('api-definition-io-list')}>
                {object.response.map((field) => (
                  <span key={field.name} className={cx('api-definition-io')}>
                    <code>{contractFieldLabel(field.code, field.name)}</code>
                  </span>
                ))}
              </span>
            </div>
            <div className={cx('api-definition-row')}>
              <small>数据来源</small>
              <span className={cx('api-definition-source')}>
                <code>{templateText === '参数适配' ? '适配' : implementation.tableOp || '查询'}</code>
                <span className={cx('api-definition-source-copy')}>
                  <strong>
                    {implementation.kind} · {sourceText}
                  </strong>
                  <small>
                    {implementation.kind === '外部服务'
                      ? '出入参按映射适配，语义加工处带函数表达式。'
                      : '语句按模板生成，字段映射沿用绑定确认结果。'}
                  </small>
                </span>
              </span>
            </div>
          </div>
        </section>
      </div>
    </section>
  )
}

import { useEffect, useState } from 'react'
import { Button, Checkbox, Input, message, Modal, Select } from 'antd'
import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { cx } from '../../utils'
import {
  useDataSources,
  EXTERNAL_PARAM_LOCATION_LABEL,
  externalDomainById,
  type ExternalApiMethod,
  type ExternalApiParamLocation,
  type ExternalApiSource
} from './catalog'

type ParamDraft = { name: string; comment: string; required: boolean; location: ExternalApiParamLocation }
type HeaderDraft = { name: string; value: string }

type Draft = {
  name: string
  description: string
  method: ExternalApiMethod
  url: string
  headers: HeaderDraft[]
  requestParams: ParamDraft[]
  responseParams: ParamDraft[]
}

/** 参数部位下拉选项：路径参数 / 查询参数 / 请求体。 */
const LOCATION_OPTIONS = (Object.keys(EXTERNAL_PARAM_LOCATION_LABEL) as ExternalApiParamLocation[]).map(
  (value) => ({ value, label: EXTERNAL_PARAM_LOCATION_LABEL[value] })
)

/** 生成空白接口草稿：新增态默认 GET；传入域地址时预填 URL 前缀，自带一条空 Headers 行便于直接填写。 */
function createDraft(domainBaseUrl = ''): Draft {
  return {
    name: '',
    description: '',
    method: 'GET',
    url: domainBaseUrl ? `${domainBaseUrl}/` : '',
    headers: [{ name: '', value: '' }],
    requestParams: [],
    responseParams: []
  }
}

/** 从已有接口初始化维护草稿。 */
function draftFromSource(source: ExternalApiSource): Draft {
  return {
    name: source.name,
    description: source.description,
    method: source.method,
    url: source.url,
    headers: source.headers.length ? source.headers.map((item) => ({ ...item })) : [{ name: '', value: '' }],
    requestParams: source.requestParams.map((item) => ({
      ...item,
      required: Boolean(item.required)
    })),
    responseParams: source.responseFields.map((item) => ({ name: item.name, comment: item.comment, required: false, location: 'body' }))
  }
}

/** 清理 Headers 草稿：过滤没有名称的行。 */
function cleanHeaders(headers: HeaderDraft[]): ExternalApiSource['headers'] {
  return headers
    .filter((item) => item.name.trim())
    .map((item) => ({ name: item.name.trim(), value: item.value.trim() }))
}

/** 清理参数行：过滤没有名称的行，规范化必填标记并保留请求部位。 */
function cleanParams(params: ParamDraft[]): ExternalApiSource['requestParams'] {
  return params
    .filter((item) => item.name.trim())
    .map((item) => ({
      name: item.name.trim(),
      comment: item.comment.trim(),
      location: item.location,
      ...(item.required ? { required: true } : {})
    }))
}

type Props = {
  /** 维护目标：接口 id（编辑）或新增态（可携带所属域 id，用于预填 URL 前缀与落域）。 */
  target: { kind: 'api'; id: string } | { kind: 'api-new'; domainId?: string }
  onBack: () => void
}

const METHOD_OPTIONS: Array<{ value: ExternalApiMethod }> = [
  { value: 'GET' },
  { value: 'POST' },
  { value: 'PUT' },
  { value: 'DELETE' }
]

/**
 * 外部 API 维护页：衔接在数据来源抽屉右侧的详情层，按 Postman 语义维护
 * 一个接口的完整定义——方法 + URL、Headers、Query、入参、出参。
 * 数据直接读写共享目录，保存/删除即时生效并返回列表。
 */
export default function ExternalApiDetailPage({ target, onBack }: Props): JSX.Element {
  const [sources, saveSources] = useDataSources()
  const source =
    target.kind === 'api'
      ? sources.find((item): item is ExternalApiSource => item.id === target.id && item.type === 'external_service') || null
      : null
  // 新增态的所属域：接口只能挂在已有域下，域地址用于预填 URL 前缀。
  const newDomain =
    target.kind === 'api-new' && target.domainId ? externalDomainById(sources, target.domainId) : null
  const [draft, setDraft] = useState<Draft>(() =>
    source ? draftFromSource(source) : createDraft(newDomain?.baseUrl || '')
  )

  // 切换维护对象（或进入新增态）时重建草稿。
  useEffect(() => {
    setDraft(source ? draftFromSource(source) : createDraft(newDomain?.baseUrl || ''))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target.kind, target.kind === 'api' ? target.id : ''])

  /** 更新一个动态行字段；通过数组拷贝保持受控更新。 */
  const updateRow = <T,>(list: T[], index: number, patch: Partial<T>): T[] =>
    list.map((item, i) => (i === index ? { ...item, ...patch } : item))

  /** 校验必填项：接口名称与 URL。 */
  const validate = (): string => {
    if (!draft.name.trim()) return '请输入接口名称。'
    if (!draft.url.trim()) return '请输入接口 URL。'
    return ''
  }

  /** 保存接口：清理空行后写回共享目录并返回列表。 */
  const save = (): void => {
    const error = validate()
    if (error) {
      message.error(error)
      return
    }
    const next: ExternalApiSource = {
      type: 'external_service',
      id: source?.id || `api-${Date.now()}`,
      domainId: source?.domainId || newDomain?.id || '',
      name: draft.name.trim(),
      description: draft.description.trim(),
      method: draft.method,
      url: draft.url.trim(),
      headers: cleanHeaders(draft.headers),
      requestParams: cleanParams(draft.requestParams),
      responseFields: cleanParams(draft.responseParams).map(({ name, comment }) => ({ name, comment }))
    }
    saveSources(source ? sources.map((item) => (item.id === next.id ? next : item)) : [...sources, next])
    message.success(source ? '外部 API 已更新' : '外部 API 已登记')
    onBack()
  }

  /** 删除接口前二次确认（仅编辑态提供）。 */
  const remove = (): void => {
    if (!source) return
    Modal.confirm({
      centered: true,
      cancelText: '取消',
      content: '将删除该接口定义；如已有应用API绑定该接口，将提示重新绑定。此操作无法恢复。',
      okButtonProps: { danger: true },
      okText: '删除',
      onOk: () => {
        saveSources(sources.filter((item) => item.id !== source.id))
        message.success('外部 API 已删除')
        onBack()
      },
      title: `删除外部 API「${source.name}」？`
    })
  }

  /** 渲染一组参数动态行：名称 + 说明 + 必填 + 删除；入参行额外带部位选择。 */
  const renderParamRows = (
    key: 'requestParams' | 'responseParams',
    rows: ParamDraft[],
    withRequired: boolean,
    withLocation = false
  ): JSX.Element => (
    <div className={cx('ds-api-rows')}>
      {rows.map((row, index) => (
        <div className={cx('ds-api-row')} key={index}>
          {withLocation ? (
            <Select
              className={cx('ds-api-row-location')}
              onChange={(value) =>
                setDraft({
                  ...draft,
                  [key]: updateRow(rows, index, { location: value as ExternalApiParamLocation })
                })
              }
              options={LOCATION_OPTIONS}
              value={row.location}
            />
          ) : null}
          <Input
            className={cx('ds-api-row-name')}
            onChange={(event) =>
              setDraft({ ...draft, [key]: updateRow(rows, index, { name: event.target.value }) })
            }
            placeholder="名称"
            value={row.name}
          />
          <Input
            className={cx('ds-api-row-comment')}
            onChange={(event) =>
              setDraft({ ...draft, [key]: updateRow(rows, index, { comment: event.target.value }) })
            }
            placeholder="说明"
            value={row.comment}
          />
          {withRequired ? (
            <Checkbox
              checked={row.required}
              onChange={(event) =>
                setDraft({ ...draft, [key]: updateRow(rows, index, { required: event.target.checked }) })
              }
            >
              必填
            </Checkbox>
          ) : null}
          <Button
            aria-label="删除参数"
            disabled={rows.length <= 1}
            icon={<DeleteOutlined />}
            onClick={() => setDraft({ ...draft, [key]: rows.filter((_, i) => i !== index) })}
            size="small"
            type="text"
          />
        </div>
      ))}
      <Button
        className={cx('ds-api-add-row')}
        icon={<PlusOutlined />}
        onClick={() =>
          setDraft({
            ...draft,
            [key]: [
              ...rows,
              { name: '', comment: '', required: false, location: 'query' as ExternalApiParamLocation }
            ]
          })
        }
        size="small"
        type="dashed"
      >
        添加
      </Button>
    </div>
  )

  /** 渲染 Headers 动态行：名称 + 取值。 */
  const renderHeaderRows = (): JSX.Element => (
    <div className={cx('ds-api-rows')}>
      {draft.headers.map((row, index) => (
        <div className={cx('ds-api-row')} key={index}>
          <Input
            className={cx('ds-api-row-name')}
            onChange={(event) =>
              setDraft({ ...draft, headers: updateRow(draft.headers, index, { name: event.target.value }) })
            }
            placeholder="名称"
            value={row.name}
          />
          <Input
            className={cx('ds-api-row-comment')}
            onChange={(event) =>
              setDraft({ ...draft, headers: updateRow(draft.headers, index, { value: event.target.value }) })
            }
            placeholder="取值"
            value={row.value}
          />
          <Button
            aria-label="删除 Header"
            disabled={draft.headers.length <= 1}
            icon={<DeleteOutlined />}
            onClick={() => setDraft({ ...draft, headers: draft.headers.filter((_, i) => i !== index) })}
            size="small"
            type="text"
          />
        </div>
      ))}
      <Button
        className={cx('ds-api-add-row')}
        icon={<PlusOutlined />}
        onClick={() => setDraft({ ...draft, headers: [...draft.headers, { name: '', value: '' }] })}
        size="small"
        type="dashed"
      >
        添加
      </Button>
    </div>
  )

  return (
    <div className={cx('ds-detail-page')}>
      <div className={cx('ds-detail-form')}>
        <label>
          <span>
            <em aria-hidden="true" className={cx('ds-editor-required')}>*</em>
            接口名称
          </span>
          <Input
            onChange={(event) => setDraft({ ...draft, name: event.target.value })}
            placeholder="例如：查询用户信息"
            value={draft.name}
          />
        </label>
        <label>
          接口说明
          <Input
            onChange={(event) => setDraft({ ...draft, description: event.target.value })}
            placeholder="一句话描述接口用途"
            value={draft.description}
          />
        </label>
        <div className={cx('ds-api-request-line')}>
          <em aria-hidden="true" className={cx('ds-editor-required')}>*</em>
          <Select
            className={cx('ds-api-method')}
            onChange={(value) => setDraft({ ...draft, method: value })}
            options={METHOD_OPTIONS}
            value={draft.method}
          />
          <Input
            className={cx('ds-api-url')}
            onChange={(event) => setDraft({ ...draft, url: event.target.value })}
            placeholder="https://host/users/{id}，路径参数用花括号占位"
            value={draft.url}
          />
        </div>
        <fieldset>
          <legend>Headers</legend>
          {renderHeaderRows()}
        </fieldset>
        <fieldset>
          <legend>入参（路径参数 / 查询参数 / 请求体）</legend>
          {renderParamRows('requestParams', draft.requestParams, true, true)}
        </fieldset>
        <fieldset>
          <legend>出参（Response）</legend>
          {renderParamRows('responseParams', draft.responseParams, false)}
        </fieldset>
      </div>
      <footer className={cx('ds-detail-footer')}>
        {source ? (
          <Button danger icon={<DeleteOutlined />} onClick={remove}>
            删除
          </Button>
        ) : null}
        <span className={cx('ds-detail-footer-spacer')} />
        <Button onClick={onBack}>取消</Button>
        <Button type="primary" onClick={save}>
          保存
        </Button>
      </footer>
    </div>
  )
}

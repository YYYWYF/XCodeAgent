import { Empty, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'

const { Paragraph, Text, Title } = Typography

type RequirementPage = {
  pageId?: string
  name?: string
  path?: string
}

type RequirementApi = {
  id: string
  name: string
  /** 接口用途说明（扁平契约的业务摘要）。 */
  summary?: string
  description?: string
  method?: string
  path?: string
  /** 需求级声明的入参：英文标识 + 中文名。 */
  request?: Array<{ code?: string; name?: string; required?: boolean; summary?: string }>
  /** 需求级声明的返回业务字段：英文标识 + 中文名（历史记录可能是纯中文名条目）。 */
  response?: Array<string | { code?: string; name?: string }>
  /** 调用该接口的应用页面 ID，展示时翻译为页面名。 */
  used_by_pages?: string[]
}

/** 契约字段展示标签：英文标识（中文名），无标识时仅中文名。 */
function contractLabel(item: string | { code?: string; name?: string }): string {
  if (typeof item === 'string') return item
  const code = String(item.code || '')
  const name = String(item.name || '')
  return code && name ? `${code}（${name}）` : name || code
}

/**
 * 需求文档中的「API契约」分栏：扁平接口文档，平铺排版——每个接口一个 h5 小节，
 * 用途段落与「入参：」「返回字段：」「调用页面：」文档行；出入参均以英文标识（中文名）
 * 声明，供开发阶段的映射绑定与生成代码沿用同一套命名；鉴权、请求体、错误码等
 * 技术细节留给计划阶段的应用API文档。
 */
export default function RequirementApis({
  apis,
  pages
}: {
  apis: RequirementApi[]
  pages: RequirementPage[]
}): ReactElement {
  const pageNameById = new Map(pages.map((page) => [String(page.pageId || ''), page.name || '']))
  return (
    <div className={cx('planning-review-document')}>
      {apis.map((api) => {
        const usedPages = (api.used_by_pages || [])
          .map((pageId) => pageNameById.get(pageId) || pageId)
          .filter(Boolean)
        const responseFields = (api.response || []).map(contractLabel).filter(Boolean)
        const requestFields = (api.request || [])
          .map((param) => `${contractLabel(param)}${param.required ? '' : ' · 可选'}`.trim())
          .filter(Boolean)
        return (
          <section key={api.id}>
            <Title level={5}>
              {api.name}
              <Text code>{` ${api.method || 'GET'} ${api.path || '/'}`}</Text>
            </Title>
            <Paragraph>{api.summary || api.description || '待补充接口用途'}</Paragraph>
            <Paragraph>
              <Text strong>入参：</Text>
              {requestFields.join('、') || '无'}
            </Paragraph>
            <Paragraph>
              <Text strong>返回字段：</Text>
              {responseFields.join('、') || '待补充'}
            </Paragraph>
            <Paragraph>
              <Text strong>调用页面：</Text>
              {usedPages.join('、') || '待补充'}
            </Paragraph>
          </section>
        )
      })}
      {!apis.length && (
        <Empty description="暂无API契约，请在需求说明书中补充" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      )}
    </div>
  )
}

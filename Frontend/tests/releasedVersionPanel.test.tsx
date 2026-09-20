import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

declare const __FRONTEND_ROOT__: string
import { test } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import ReleasedVersionPanel from '../src/renderer/src/components/AiChatPanel/components/ReleasedVersionPanel'
import {
  shouldInjectPlanningPlaceholder,
  shouldShowRightWorkspace
} from '../src/renderer/src/components/AiChatPanel/utils'
import RightPanelTabs from '../src/renderer/src/components/AiChatPanel/components/RightPanelTabs'
import type { ApplicationConfig } from '../src/renderer/src/typings'

/** 最小可用应用配置：只提供该面板实际读取的字段。 */
function application(): ApplicationConfig {
  return {
    id: 'app-1',
    appName: '欢迎页',
    workspaceRoot: '/workspace'
  } as ApplicationConfig
}

test('已生成版本面板默认渲染应用文件，且不出现会话相关结构', () => {
  const html = renderToStaticMarkup(
    <ReleasedVersionPanel application={application()} workspaceRoot="/workspace" />
  )

  // 两个只读入口都在。
  assert.ok(html.includes('应用文件'), '缺少「应用文件」页签')
  assert.ok(html.includes('应用预览'), '缺少「应用预览」页签')
  // 默认选中应用文件：它渲染的是文件树面板，而不是预览 iframe。
  assert.ok(!html.includes('<iframe'), '默认不应渲染预览 iframe')
  // 只读视图不得带出会话侧栏/输入框等对话结构。
  assert.ok(!html.includes('ai-chat-assistant'), '不应渲染对话助手区')
})

test('应用文件面板拿到工作区路径后渲染文件浏览器', () => {
  const html = renderToStaticMarkup(
    <ReleasedVersionPanel application={application()} workspaceRoot="/workspace" />
  )
  // SourcePanel 在拿到 workspaceRoot 时进入加载态，而不是空态提示。
  assert.ok(!html.includes('暂无应用文件'), '有工作区时不应落到空态')
})

test('最左侧窄栏：顶部 4 个抽屉入口 + 底部 4 个入口（含用户）', () => {
  const html = renderToStaticMarkup(
    <ReleasedVersionPanel application={application()} workspaceRoot="/workspace" />
  )
  // 入口是纯图标，文案只出现在 title / aria-label 上（悬停提示），不作为可见文本。
  const rail = html.slice(
    html.indexOf('released-version-rail'),
    html.indexOf('released-version-main')
  )
  for (const label of ['任务管理', '异步任务', '潮汐任务', '数据来源', '文件', '技能', '设置']) {
    assert.ok(rail.includes(`title="${label}"`), `窄栏缺少「${label}」入口`)
    assert.ok(!rail.includes(`>${label}<`), `「${label}」不应作为可见文案常驻窄栏（占宽度）`)
  }
  assert.ok(html.includes('session-user'), '窄栏缺少用户头像')
})

test('目录树在右侧并列出规划文档，默认不置灰', () => {
  const html = renderToStaticMarkup(
    <ReleasedVersionPanel application={application()} workspaceRoot="/workspace" />
  )
  assert.ok(html.includes('目录树'), '缺少目录树栏目标题')
  // 有 Markdown 形态的规划文档都应在树中；UI 设计稿是结构化 JSON，不进文档分组。
  for (const label of ['需求文档', '产品规划', '技术规划']) {
    assert.ok(html.includes(label), `目录树缺少「${label}」`)
  }
  assert.ok(!html.includes('UI 设计'), '结构化产物不应混进文档分组')
  // 探测未完成前不得把文档标成缺失，否则首帧会闪一片灰。
  assert.ok(!html.includes('is-missing'), '探测完成前不应置灰')
})

test('历史版本不展示右侧工作区页签，当前版本保持原样', () => {
  const decide = (versionReadOnly: boolean): boolean =>
    shouldShowRightWorkspace({ versionReadOnly, rightPanelOpen: true, hasRightPanel: true })

  // 历史版本：右侧那排"开发产物/预览/源码"页签整块不展示，只留内容区自带的目录树。
  assert.equal(decide(true), false, '历史版本不应展示右侧工作区页签')
  // 当前版本：行为完全不变。
  assert.equal(decide(false), true, '当前版本必须保持展示右侧工作区')

  // 常规开关仍然生效，不能被这次改动抹掉。
  assert.equal(
    shouldShowRightWorkspace({
      versionReadOnly: false,
      rightPanelOpen: false,
      hasRightPanel: true
    }),
    false
  )
  assert.equal(
    shouldShowRightWorkspace({
      versionReadOnly: false,
      rightPanelOpen: true,
      hasRightPanel: false
    }),
    false
  )
})

test('文档存在性探测的读取量必须满足后端约束', () => {
  // 后端 ReadFileRequest.max_chars 的下限是 200，低于它请求会被校验层拒绝；
  // 探测一旦失败就会把"参数非法"误判成"文档不存在"，三份文档全被置灰。
  const backendMinMaxChars = 200
  const probeChars = readProbeCharsFromSource()
  assert.ok(
    probeChars >= backendMinMaxChars,
    `探测读取量 ${probeChars} 低于后端下限 ${backendMinMaxChars}：文档会被误判为不存在`
  )
  assert.ok(probeChars <= 200000, `探测读取量 ${probeChars} 超过后端上限`)
})

/** 从 SourcePanel 源码里读回 PROBE_CHARS，避免测试与实现各写一份常量。 */
function readProbeCharsFromSource(): number {
  const source = readFileSync(
    `${__FRONTEND_ROOT__}/src/renderer/src/components/AiChatPanel/components/SourcePanel/index.tsx`,
    'utf-8'
  )
  const matched = source.match(/const PROBE_CHARS = (\d+)/)
  assert.ok(matched, '未找到 PROBE_CHARS 定义')
  return Number(matched[1])
}

test('三份规划文档各有不同的图标与色调', () => {
  const html = renderToStaticMarkup(
    <ReleasedVersionPanel application={application()} workspaceRoot="/workspace" />
  )
  // 每份文档一个色调类，便于在树里区分；缺失时不再各自强调颜色。
  for (const tone of ['is-requirement', 'is-product', 'is-technical']) {
    assert.ok(html.includes(tone), `文档缺少色调类 ${tone}`)
  }
  // 三份文档的图标应当不同：统计这段里的 anticon 名称，至少出现三种。
  const icons = new Set(
    (html.match(/anticon-[a-z-]+/g) ?? []).filter((name) =>
      ['anticon-file-text', 'anticon-profile', 'anticon-code'].includes(name)
    )
  )
  assert.equal(icons.size, 3, `三份文档应使用三种不同图标，实际 ${[...icons].join(', ')}`)
})

test('窄栏顶部入口使用与 prototype 一致的图标', () => {
  const html = renderToStaticMarkup(
    <ReleasedVersionPanel application={application()} workspaceRoot="/workspace" />
  )
  const rail = html.slice(
    html.indexOf('released-version-rail'),
    html.indexOf('released-version-main')
  )
  // 异步=沙漏、潮汐=月亮、数据来源=数据库；三者形状不同才便于区分。
  assert.ok(rail.includes('anticon-hourglass'), '异步任务应为沙漏图标')
  assert.ok(rail.includes('anticon-moon'), '潮汐任务应为月亮图标')
  assert.ok(rail.includes('anticon-database'), '数据来源应为数据库图标')
  // 任务管理用 SVG 资源图标（与工作台窄栏同款），不是 antd 图标。
  assert.ok(rail.includes('released-version-rail-asset-icon'), '任务管理应为 SVG 资源图标')
})

test('历史版本不展示关闭按钮，当前版本仍有', () => {
  // 关闭按钮只在传入 onClose 时渲染：只读回看没有"关闭面板"语义。
  const withClose = renderToStaticMarkup(
    <ReleasedVersionPanel application={application()} workspaceRoot="/workspace" />
  )
  assert.ok(!withClose.includes('workspace-tabs-close'), '历史版本不应展示右侧 tab 行的关闭按钮')
})

test('RightPanelTabs：不传 onClose 时不渲染关闭按钮', () => {
  const withoutClose = renderToStaticMarkup(
    <RightPanelTabs
      tabs={[{ key: 'source', label: '应用文件', available: true }]}
      active="source"
      onChange={() => undefined}
    />
  )
  assert.ok(!withoutClose.includes('workspace-tabs-close'), '不传 onClose 时不应有关闭按钮')
  assert.ok(withoutClose.includes('应用文件'), '页签本身仍应渲染')

  const withClose = renderToStaticMarkup(
    <RightPanelTabs
      tabs={[{ key: 'source', label: '应用文件', available: true }]}
      active="source"
      onChange={() => undefined}
      onClose={() => undefined}
    />
  )
  assert.ok(withClose.includes('workspace-tabs-close'), '传了 onClose 时应渲染关闭按钮')
})

test('等用户输入新迭代需求时不注入 loading 占位', () => {
  const decide = (over: Partial<Parameters<typeof shouldInjectPlanningPlaceholder>[0]> = {}) =>
    shouldInjectPlanningPlaceholder({ messageCount: 0, stage: 'ready_for_workbench', hasWorkflow: true, ...over })

  // 收集需求且本轮还没开始 workflow：那一轮不会有任何帧到达，注入占位会一直转圈。
  // pending 与 awaiting_user 都要覆盖：后端写盘的是 pending，只有前端内存里才是 awaiting_user。
  assert.equal(
    decide({ stage: 'collecting_requirement', hasWorkflow: false }),
    false,
    'pending 的收集需求态不应注入占位'
  )
  assert.equal(
    decide({ stage: 'collecting_requirement', hasWorkflow: false }),
    false,
    'awaiting_user 的收集需求态同样不应注入占位'
  )
  // 已经跑起 workflow 时占位是必要的（避免只剩 Agent 头像）。
  assert.equal(decide({ stage: 'collecting_requirement', hasWorkflow: true }), true)
  // 已有消息时永远不注入。
  assert.equal(decide({ messageCount: 1 }), false)
  // 其它阶段照常注入。
  assert.equal(decide({ stage: 'generating_requirement_document' }), true)
})

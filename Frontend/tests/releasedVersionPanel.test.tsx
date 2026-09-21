import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

declare const __FRONTEND_ROOT__: string
import { test } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import ReleasedVersionPanel from '../src/renderer/src/components/AiChatPanel/components/ReleasedVersionPanel'
import {
  revisionPreviewPresentation,
  shouldRunRevisionPreview
} from '../src/renderer/src/components/AiChatPanel/components/ReleasedVersionPanel/useRevisionPreview'
import {
  shouldInjectPlanningPlaceholder,
  shouldShowRightWorkspace
} from '../src/renderer/src/components/AiChatPanel/utils'
import RightPanelTabs from '../src/renderer/src/components/AiChatPanel/components/RightPanelTabs'
import BrowserPreviewPanel from '../src/renderer/src/components/BrowserPreviewPanel/BrowserPreviewPanel'
import {
  previewServiceBadge,
  shouldAutoStartPreviewService
} from '../src/renderer/src/components/BrowserPreviewPanel/serviceStatusPolicy'
import type { ApplicationConfig } from '../src/renderer/src/typings'

/** 最小可用应用配置：只提供该面板实际读取的字段。 */
function application(): ApplicationConfig {
  return {
    id: 'app-1',
    appName: '欢迎页',
    workspaceRoot: '/workspace'
  } as ApplicationConfig
}

/** 预览面板要读菜单与页面清单，缺了会直接抛错；这份是给它的最小配置。 */
function previewApplication(): ApplicationConfig {
  return {
    id: 'app-1',
    appName: '欢迎页',
    workspaceRoot: '/workspace',
    menus: { items: [] },
    pages: []
  } as unknown as ApplicationConfig
}

test('历史版本预览：工具栏角标报运行中且不可点开抽屉', () => {
  // 这条守的是接线而不只是策略函数：页面已经渲染出来了，角标却报"待启动"。
  const html = renderToStaticMarkup(
    <BrowserPreviewPanel
      application={previewApplication()}
      externalServiceStatus="running"
      previewBaseUrl="http://localhost:3001"
      selectedPagePath="/"
    />
  )
  const badge = html.slice(
    html.indexOf('browser-service-status-button'),
    html.indexOf('browser-navigation')
  )
  assert.ok(badge.includes('运行中'), '历史版本预览已就绪时角标应报运行中')
  assert.ok(!badge.includes('待启动'), '不应再报待启动')
  assert.ok(badge.includes('is-running'), '角标样式应跟随运行态')
  assert.ok(badge.includes('disabled'), '历史版本下不应可点开服务状态抽屉')
})

test('当前版本预览：不传外部状态时角标行为不变', () => {
  const html = renderToStaticMarkup(
    <BrowserPreviewPanel
      application={previewApplication()}
      previewBaseUrl="http://localhost:3000"
      selectedPagePath="/"
    />
  )
  const badge = html.slice(
    html.indexOf('browser-service-status-button'),
    html.indexOf('browser-navigation')
  )
  // 没有运行时句柄也没有外部状态时照旧报待启动 —— 这条老行为不能被改动抹掉。
  assert.ok(badge.includes('待启动'), '无外部状态时应照旧报待启动')
  assert.ok(badge.includes('is-idle'), '角标样式应保持 idle')
})

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

test('历史版本的预览用该版本自己的地址，没有 tag 时退回工作区预览', () => {
  const decide = (
    over: Partial<Parameters<typeof revisionPreviewPresentation>[0]> = {}
  ): ReturnType<typeof revisionPreviewPresentation> =>
    revisionPreviewPresentation({ url: '', error: '', ...over })

  // 就绪后必须用该版本 dev server 的地址 —— 用工作区的地址就是回到"看到最新版本"的老问题。
  assert.deepEqual(decide({ revision: 'v1.0', url: 'http://localhost:3001/' }), {
    kind: 'revision',
    url: 'http://localhost:3001/'
  })
  // 没有 tag 的旧记录退回工作区预览，行为与引入历史预览之前完全一致。
  assert.deepEqual(decide({ url: 'http://localhost:3000/' }), { kind: 'workspace' })
  assert.deepEqual(decide({}), { kind: 'workspace' })
})

test('历史版本预览未就绪时只给加载态，不落到空白 iframe', () => {
  const decide = (
    over: Partial<Parameters<typeof revisionPreviewPresentation>[0]> = {}
  ): ReturnType<typeof revisionPreviewPresentation> =>
    revisionPreviewPresentation({ url: '', error: '', ...over })

  // 还没开始启动与正在物化/安装/启动都只该看到加载态：提前渲染 about:blank 的
  // iframe 会让人以为预览坏了，而实际上它正在跑。
  assert.deepEqual(decide({ revision: 'v1.0' }), { kind: 'loading' })
  // 启动失败给出错误与重试入口，而不是停在加载态里转圈。
  assert.deepEqual(decide({ revision: 'v1.0', error: '依赖安装失败' }), {
    kind: 'error',
    message: '依赖安装失败'
  })
  // 错误优先于加载态：两者同时成立说明上一次启动已经失败，不该继续转圈。
  assert.deepEqual(decide({ revision: 'v1.0', url: '', error: '依赖安装失败' }), {
    kind: 'error',
    message: '依赖安装失败'
  })
})

test('历史版本预览只在预览 tab 激活时启动（懒启动）', () => {
  const decide = (over: Partial<Parameters<typeof shouldRunRevisionPreview>[0]> = {}): boolean =>
    shouldRunRevisionPreview({
      enabled: true,
      revision: 'v1.0',
      workspaceRoot: '/workspace',
      ...over
    })

  assert.equal(decide(), true, '预览 tab + 有 tag + 有工作区时应启动')
  // 停留在「应用文件」tab 上翻文件不该白白拉起一个 dev server。
  assert.equal(decide({ enabled: false }), false, '非预览 tab 不应启动历史版本预览')
  // 没有 tag 的旧版本没有可物化的对象，退回工作区预览那条路。
  assert.equal(decide({ revision: undefined }), false, '缺少 tag 时不应启动')
  assert.equal(decide({ revision: '' }), false, 'tag 为空串时不应启动')
  assert.equal(decide({ workspaceRoot: '' }), false, '缺少工作区路径时不应启动')
})

test('历史版本不自动拉起工作区预览服务', () => {
  const decide = (
    over: Partial<Parameters<typeof shouldAutoStartPreviewService>[0]> = {}
  ): boolean =>
    shouldAutoStartPreviewService({
      activeTabIsPreview: true,
      hasSnapshot: true,
      busy: false,
      status: 'idle',
      alreadyRequested: false,
      ...over
    })

  // 当前版本：idle 时照旧自动启动，这条老行为不能被改动抹掉。
  assert.equal(decide(), true, '当前版本在 idle 时仍应自动启动')
  assert.equal(decide({ hasRevision: true }), false, '历史版本不应拉起工作区预览服务')
  // 历史版本的判断优先级要高于状态判断：即使服务正跑着，也不该被这个入口再动一次。
  assert.equal(decide({ hasRevision: true, status: 'running' }), false)
  // 常规边界仍然生效。
  assert.equal(decide({ status: 'failed' }), false)
  assert.equal(decide({ busy: true }), false)
  assert.equal(decide({ hasSnapshot: false }), false)
  assert.equal(decide({ alreadyRequested: true }), false)
})

test('历史版本预览的服务角标报"运行中"，且不可点开抽屉', () => {
  const decide = (
    over: Partial<Parameters<typeof previewServiceBadge>[0]> = {}
  ): ReturnType<typeof previewServiceBadge> =>
    previewServiceBadge({ hasServiceControl: false, runtimeStatus: 'idle', ...over })

  // 页面已经渲染出来了，角标却报"待启动"就是这个 bug：外部状态必须优先。
  const revision = decide({ externalStatus: 'running' })
  assert.equal(revision.label, '运行中', '历史版本预览已就绪时角标应报运行中')
  assert.equal(revision.status, 'running')
  // 抽屉里的重启/诊断指向工作区那套进程，对历史版本的 dev server 不成立。
  assert.equal(revision.interactive, false, '历史版本不应可点开服务状态抽屉')
  assert.ok(!revision.tooltip.includes('重启'), '不该再提示重启/诊断')
})

test('当前版本的角标行为完全不变', () => {
  const decide = (
    over: Partial<Parameters<typeof previewServiceBadge>[0]> = {}
  ): ReturnType<typeof previewServiceBadge> =>
    previewServiceBadge({ hasServiceControl: true, runtimeStatus: 'idle', ...over })

  assert.equal(decide().label, '待启动')
  assert.equal(decide().interactive, true, '当前版本应可点开服务状态抽屉')
  assert.ok(decide().tooltip.includes('重启'), '当前版本仍提示重启/诊断')
  // 各状态文案照旧。
  assert.equal(decide({ runtimeStatus: 'running' }).label, '运行中')
  assert.equal(decide({ runtimeStatus: 'starting' }).label, '启动中')
  assert.equal(decide({ runtimeStatus: 'failed' }).label, '需处理')
  // 没有运行时句柄时照旧不可点（原有行为）。
  assert.equal(decide({ hasServiceControl: false }).interactive, false)
})

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

test('带 tag 的历史版本仍能正常渲染面板', () => {
  // 新增的历史预览接线会在面板里挂一个 hook；这条守住"传了 revision 不会渲染失败"，
  // 因为历史版本入口必然带 tag，一旦这里抛错整块回看就白屏。
  const html = renderToStaticMarkup(
    <ReleasedVersionPanel application={application()} revision="v1.0" workspaceRoot="/workspace" />
  )
  assert.ok(html.includes('应用文件'), '带 tag 时仍应渲染两个只读入口')
  assert.ok(html.includes('目录树'), '带 tag 时仍应渲染目录树')
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
    shouldInjectPlanningPlaceholder({
      messageCount: 0,
      stage: 'ready_for_workbench',
      hasWorkflow: true,
      ...over
    })

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

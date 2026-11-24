import assert from 'node:assert/strict'
import { test } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { RequirementFlowsSection, RequirementOverviewSection, RequirementPagesSection } from '../src/renderer/src/components/AiChatPanel/components/DocPanel/RequirementDocSections'
import type { JsonRecord } from '../src/renderer/src/components/AiChatPanel/components/DocPanel/RequirementDocPanelData'
import ProductPlanDocPanel from '../src/renderer/src/components/AiChatPanel/components/DocPanel/ProductPlanDocPanel'
import PageDesignOverviewSection from '../src/renderer/src/components/AiChatPanel/components/DocPanel/PageDesignOverviewSection'
import TechnicalPlanDocPanel from '../src/renderer/src/components/AiChatPanel/components/DocPanel/TechnicalPlanDocPanel'
import './technicalPlanTypes.test'

/** 一份包含所有可选区块的页面规划，用于确认重做后内容一条不少。 */
function productPlan(): JsonRecord {
  return {
    pages: [
      {
        pageId: 'hello_world',
        name: 'Hello World',
        path: '/page/hello-world',
        module_id: 'mode_project_mgmt',
        description: '展示写死的字符串的欢迎页面',
        goal: '向用户展示固定的欢迎信息',
        information_items: [{ itemId: 't1', label: '欢迎信息', description: '固定字符串' }],
        actions: [
          {
            actionId: 'a1',
            name: '刷新',
            description: '重新加载',
            behavior: { type: 'business', expectedResult: '内容刷新' }
          }
        ],
        navigation_targets: ['/page/home'],
        state_requirements: { loading: '短暂显示加载态', error: '不适用' },
        acceptance_criteria: ['能看到欢迎语', '不依赖后端']
      }
    ]
  }
}

test('页面卡不再展示原始 module_id', () => {
  const html = renderToStaticMarkup(
    <RequirementPagesSection productPlan={productPlan()} sectionKey="pages" spec={{}} />
  )
  assert.ok(!html.includes('mode_project_mgmt'), '不应出现原始 module_id')
})

test('页面卡保留产品内容与序号磁贴，UI 状态不进入产品文档', () => {
  const html = renderToStaticMarkup(
    <RequirementPagesSection productPlan={productPlan()} sectionKey="pages" spec={{}} />
  )
  for (const text of [
    'Hello World',
    '/page/hello-world',
    '展示写死的字符串的欢迎页面',
    '页面目标',
    '向用户展示固定的欢迎信息',
    '业务信息',
    '欢迎信息',
    '核心操作',
    '刷新',
    '页面跳转',
    '/page/home',
    '产品验收标准',
    '能看到欢迎语',
    '不依赖后端'
  ]) {
    assert.ok(html.includes(text), `重做后缺少内容：${text}`)
  }
  assert.ok(html.includes('requirement-doc-page-index'), '缺少序号徽章')
  assert.ok(html.includes('requirement-doc-page-tile'), '业务信息应是磁贴')
  assert.ok(html.includes('requirement-doc-page-zone'), '缺少内嵌区块')
  assert.ok(html.includes('is-muted'), '规则区应带弱化标记')
  assert.ok(!html.includes('状态要求') && !html.includes('短暂显示加载态'), 'UI 状态由设计稿表达')
})

test('独立产品规划复用页面可视化，并保留摘要、状态、版本和产品级验收标准', () => {
  const html = renderToStaticMarkup(
    <ProductPlanDocPanel
      productPlan={{
        ...productPlan(),
        app: { name: '欢迎应用', summary: '单页欢迎应用摘要' },
        confirmation_status: 'confirmed',
        version: '0.1.0',
        product_acceptance_criteria: ['应用可正常打开']
      }}
    />
  )
  for (const text of [
    '欢迎应用',
    '单页欢迎应用摘要',
    '已确认',
    '0.1.0',
    '页面与用户操作',
    '向用户展示固定的欢迎信息',
    '内容刷新',
    '能看到欢迎语',
    '产品级验收标准',
    '应用可正常打开'
  ]) {
    assert.ok(html.includes(text), `产品规划缺少内容：${text}`)
  }
  assert.ok(html.includes('requirement-doc-page-tile'), '产品规划应复用设计阶段的页面卡片')
})

test('空产品规划展示现有空态，待确认状态仍可见', () => {
  const html = renderToStaticMarkup(
    <ProductPlanDocPanel productPlan={{ confirmation_status: 'pending_user_confirmation' }} />
  )
  for (const text of ['未命名应用', '待确认', '暂无页面规划', '暂无产品级验收标准']) {
    assert.ok(html.includes(text), `空产品规划缺少内容：${text}`)
  }
})

test('页面概览按交互和布局子区域打标，不继承路由导致的页面级修改', () => {
  const specDiff = {
    pages: [{ pageId: 'hello_world', _change_status: 'modified' }],
    _page_design_overview: {
      hello_world: { interaction: 'unchanged', layout: 'unchanged' }
    }
  }
  const routeOnly = renderToStaticMarkup(
    <PageDesignOverviewSection
      productPlan={productPlan()}
      sectionKey="page-design-overview"
      spec={{}}
      specDiff={specDiff}
    />
  )
  assert.equal(routeOnly.split('is-modified').length - 1, 0, '只改路由时两个子区域都不能标修改')

  const interactionChanged = renderToStaticMarkup(
    <PageDesignOverviewSection
      productPlan={productPlan()}
      sectionKey="page-design-overview"
      spec={{}}
      specDiff={{
        ...specDiff,
        _page_design_overview: {
          hello_world: { interaction: 'modified', layout: 'unchanged' }
        }
      }}
    />
  )
  assert.equal(interactionChanged.split('is-modified').length - 1, 1, '真实交互变化仍须标修改')
})

test('页面风格按维度内容比较，应用摘要新增业务功能不误标修改', () => {
  const baseline = { app_info: { summary: '桌面端，侧边栏导航，表格。' } }
  const specDiff = { _app_change_status: 'modified', pages: [] }
  const sameStyle = renderToStaticMarkup(
    <PageDesignOverviewSection
      productPlan={{ pages: [] }}
      sectionKey="page-design-overview"
      spec={{ app_info: { summary: '桌面端，侧边栏导航，表格。新增问候页。' } }}
      specBaseline={baseline}
      specDiff={specDiff}
    />
  )
  assert.equal(sameStyle.split('is-modified').length - 1, 0, '相同风格维度必须保持未变更')

  const changedStyle = renderToStaticMarkup(
    <PageDesignOverviewSection
      productPlan={{ pages: [] }}
      sectionKey="page-design-overview"
      spec={{ app_info: { summary: '移动端，侧边栏导航，表格。新增问候页。' } }}
      specBaseline={baseline}
      specDiff={specDiff}
    />
  )
  assert.equal(changedStyle.split('is-modified').length - 1, 1, '真实终端风格变化仍须标修改')
})

test('删除页面及其模块和流程继续以墓碑形式展示在各需求页签', () => {
  const deletedPage = {
    pageId: 'greeting_page', name: '问候展示页', path: '/page/greeting',
    description: '展示晚上好', _change_status: 'deleted'
  }
  const deletedModule = {
    id: 'greeting_module', name: '问候展示', description: '问候功能', _change_status: 'deleted'
  }
  const deletedFlow = {
    id: 'greeting_flow', name: '问候流程', description: '展示问候',
    steps: ['打开问候页'], _change_status: 'deleted'
  }
  const specDiff = {
    pages: [deletedPage], feature_modules: [deletedModule], business_flows: [deletedFlow],
    _page_design_overview: {
      greeting_page: { interaction: 'deleted', layout: 'deleted' }
    }
  }
  const views = [
    renderToStaticMarkup(<RequirementOverviewSection sectionKey="overview" spec={{}} specDiff={specDiff} />),
    renderToStaticMarkup(<RequirementPagesSection productPlan={{}} sectionKey="pages" spec={{}} specDiff={specDiff} />),
    renderToStaticMarkup(<RequirementFlowsSection sectionKey="flows" spec={{}} specDiff={specDiff} />),
    renderToStaticMarkup(
      <PageDesignOverviewSection
        productPlan={{}}
        sectionKey="page-design-overview"
        spec={{}}
        specDiff={specDiff}
      />
    )
  ]
  for (const [index, expected] of ['问候展示', '问候展示页', '问候流程', '问候展示页'].entries()) {
    assert.ok(views[index].includes(expected), `第 ${index + 1} 个页签缺少删除条目`)
    assert.ok(views[index].includes('待删除'), `第 ${index + 1} 个页签缺少删除徽标`)
  }
  assert.ok(views[1].includes('/page/greeting'), '删除页应保留原路由供识别')
})

test('技术规划沿用计划阶段的章节导航与无接口空态', () => {
  const html = renderToStaticMarkup(<TechnicalPlanDocPanel plan={{}} productPlan={{}} />)
  for (const text of ['架构', '实体', 'API 契约', '页面绑定', 'technical-plan-doc-panel']) {
    assert.ok(html.includes(text), `技术规划缺少章节：${text}`)
  }
})

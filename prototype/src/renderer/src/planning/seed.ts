import type { ApplicationConfig } from '../typings'
import type { InitializationPlanningArtifacts, InitializationPlanningSeed } from './model'

/** 将演示业务种子绑定到实际应用配置，配置影响产物但不写入任何连接凭据。 */
export function personalizePlanningSeed(
  application: ApplicationConfig,
  seed: InitializationPlanningSeed
): InitializationPlanningArtifacts {
  const artifacts = structuredClone(seed)
  const name = application.name || application.appName
  const description = application.senario || artifacts.requirementSpec.app_info?.description || ''
  artifacts.requirementSpec.app_info = {
    ...artifacts.requirementSpec.app_info,
    name,
    description,
    terminal: application.terminal,
    layout: application.layout.type
  }
  artifacts.productPlan.app = { ...artifacts.productPlan.app, name, summary: description }
  artifacts.requirementSpec.authorization_requirements = {
    ...artifacts.requirementSpec.authorization_requirements,
    enabled: application.auth.enable,
    restrictedPages: artifacts.requirementSpec.authorization_requirements?.restrictedPages || [],
    restrictedOperations:
      artifacts.requirementSpec.authorization_requirements?.restrictedOperations || []
  }
  const source = application.datasource.type
  artifacts.technicalPlan.architecture = {
    ...artifacts.technicalPlan.architecture,
    data:
      source === 'None'
        ? '前端本地静态演示数据；无需数据库连接。'
        : source === 'API'
          ? '通过外部 API 获取业务数据；字段关系在实体设计阶段确认。'
          : '使用数据库存储业务数据；连接与字段关系在实体设计阶段确认。'
  }
  artifacts.technicalPlan.data_source_type = source
  return artifacts
}

/** 将已确认产品事实投影到 UI 与技术规划方案，稳定身份保持不变。 */
export function synchronizePlanningReferences(artifacts: InitializationPlanningArtifacts): void {
  const pages = artifacts.productPlan.pages || []
  artifacts.uiDesigns.pages = pages.map((page: Record<string, any>) => {
    const previous = artifacts.uiDesigns.pages.find((item) => item.pageId === page.pageId)
    return {
      pageId: page.pageId,
      name: page.name,
      path: page.path,
      page_key: previous?.page_key || page.pageId,
      template: previous?.template || '内容卡片',
      status: previous?.status || 'queued',
      description: previous?.description || page.goal || page.description,
      sections: previous?.sections || (page.information_items || []).map((item: any) => item.label),
      variant: previous?.variant || 0,
      accent: previous?.accent || 'violet'
    }
  })
  const technicalPages = artifacts.technicalPlan.pages || []
  artifacts.technicalPlan.pages = pages.map((page: Record<string, any>) => ({
    ...technicalPages.find((item: any) => item.pageId === page.pageId),
    pageId: page.pageId,
    name: page.name,
    path: page.path,
    references: technicalPages.find((item: any) => item.pageId === page.pageId)?.references || {
      endpoint_dependencies: [],
      action_implementations: []
    }
  }))
}

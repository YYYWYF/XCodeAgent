import { randomUUID } from '@ag-ui/client'
import type { ApplicationConfig } from '../typings'
import { AgUiChatSession } from './agUiAgent'


// 读取创建规划两节点 Graph 的 AG-UI 地址。
function getApplicationPlanningUrl(): string {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/application-page-planning/run`
    : '/api/agent/application-page-planning/run'
}


// 为一次新建应用规划会话创建稳定线程标识。
export function createPagePlanningThreadId(): string {
  return randomUUID()
}


// 创建复用标准 Workflow AG-UI 协议的独立规划会话。
export function createApplicationPlanningSession(threadId: string): AgUiChatSession {
  return new AgUiChatSession(threadId, getApplicationPlanningUrl())
}


// 把创建表单配置转换为 requirements 节点可直接分析的原始需求。
export function buildApplicationPlanningRequest(application: ApplicationConfig): string {
  // 兼容旧应用索引只在 schema 内保存完整配置的情况，避免规划弹窗因缺字段直接白屏。
  const schema = application.schema || ({} as ApplicationConfig['schema'])
  const appName = application.appName || schema.appName || application.name || '未命名应用'
  const scenario = application.senario || schema.senario || '用户暂未补充场景说明。'
  const terminal = application.terminal || schema.terminal || 'PC'
  const layout = application.layout || schema.layout || {
    type: '',
    useHeader: true,
    useFooter: false
  }
  const datasource = application.datasource?.type || schema.datasource?.type || 'None'
  const authEnabled = application.auth?.enable ?? schema.auth?.enable ?? false
  return [
    `请为新应用「${appName}」完成需求确认和项目规划。`,
    `应用场景：${scenario}`,
    `目标终端：${terminal}。`,
    `导航布局：${layout.type || '由规划阶段确定'}，页头=${layout.useHeader ? '启用' : '禁用'}，页脚=${layout.useFooter ? '启用' : '禁用'}。`,
    `数据源偏好：${datasource}。`,
    `认证：${authEnabled ? '启用' : '不启用'}。`,
    '本轮只完成 RequirementSpec 和 ProjectPlan，不生成页面细节或代码。'
  ].join('\n')
}

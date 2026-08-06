// 武汉分行项目管理系统 · 开发镜像会话历史。
// my-projects 已设计完成的对话历史（设计确认 → 构建 → 验收）。
import type { EditorMode } from '../../src/renderer/src/typings'

type Msg = { id: number; role: 'user' | 'assistant'; content: string; workflow?: Record<string, unknown>; processSteps?: unknown[]; createdAt: number }

function integrationChecks() {
  return [
    { id: 'check-contract', name: 'API 契约一致性校验', status: 'passed', required: true, evidence: '响应字段与契约定义一致。' },
    { id: 'check-route', name: '页面路由注册校验', status: 'passed', required: true, evidence: '路由 /my-projects 已挂载，可正常访问。' },
    { id: 'check-render', name: '页面渲染校验', status: 'passed', required: false, evidence: '页面无运行时报错，关键交互可用。' }
  ]
}

// 返回指定工作区 + 编辑器模式下的全部会话记录。
export function mockChatSessions(workspaceRoot: string, editorMode: EditorMode): unknown[] {
  if (editorMode !== 'frontend') return []
  const base = Date.now() - 86400_000
  const pageId = 'my-projects'
  const pageLabel = '我的项目'

  const steps = [
    { id: 'step-inspect', kind: 'workflow', status: 'completed', title: '检查工作区结构', detail: '已扫描前端工程目录、入口与既有约定。', sequence: 1 },
    { id: 'step-prepare', kind: 'workflow', status: 'completed', title: '规划构建任务（DAG）', detail: `已为「${pageLabel}」拆解构建任务并编译执行 DAG。`, sequence: 2 },
    {
      id: 'step-build', kind: 'workflow', status: 'completed', title: '生成页面代码', detail: '', sequence: 3,
      buildExecutionSlice: {
        scope: { type: 'page', id: pageId, label: pageLabel },
        target_unit_ids: [`page:${pageId}`],
        tasks: [
          { id: `task-${pageId}-0`, task_id: `task-${pageId}-0`, unit_id: `page:${pageId}`, owner: 'frontend', title: `新增 ${pageLabel} 页面组件`, status: 'completed', target_files: [`frontend/src/pages/${pageId}/index.tsx`] },
          { id: `task-${pageId}-1`, task_id: `task-${pageId}-1`, unit_id: `page:${pageId}`, owner: 'frontend', title: '对接 GET /api/projects/my', status: 'completed' },
          { id: `task-${pageId}-2`, task_id: `task-${pageId}-2`, unit_id: `page:${pageId}`, owner: 'frontend', title: `注册路由 /${pageId}`, status: 'completed' }
        ],
        summary: { total: 3, completed: 3, pending: 0, running: 0, failed: 0 }
      }
    },
    { id: 'step-test', kind: 'workflow', status: 'completed', title: '执行集成测试', detail: '', sequence: 4, checks: integrationChecks() }
  ]

  const messages: Msg[] = [
    { id: 1, role: 'user', content: `为「${pageLabel}」生成页面详细设计`, createdAt: base + 1000 },
    { id: 2, role: 'assistant', content: `已为「${pageLabel}」生成页面设计，请确认后开始构建。`, createdAt: base + 2000, workflow: { summary: { phase: 'detail_confirmation', status: 'completed' }, events: [] } as Record<string, unknown> },
    { id: 3, role: 'user', content: '确认设计并进入构建', createdAt: base + 3000 },
    {
      id: 4, role: 'assistant', content: `集成测试已通过，预览已启动。\n\n请在右侧预览查看「${pageLabel}」效果，确认验收。`, createdAt: base + 4000,
      workflow: { summary: { phase: 'launch_project', status: 'completed' }, events: [] } as Record<string, unknown>,
      processSteps: steps
    }
  ]

  return [
    {
      id: `session-${pageId}`,
      title: `页面会话：${pageLabel}`,
      editorMode: 'frontend',
      threadId: `thread-${pageId}`,
      pageId,
      workspaceRoot,
      messages,
      createdAt: base,
      updatedAt: base + 4000
    }
  ]
}

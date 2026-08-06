// 武汉分行项目管理系统 · 设计镜像会话历史（需求澄清进行中）。
import type { EditorMode } from '../../src/renderer/src/typings'

type Msg = { id: number; role: 'user' | 'assistant'; content: string; createdAt: number }

// 返回指定工作区 + 编辑器模式下的全部会话记录。
export function mockChatSessions(workspaceRoot: string, editorMode: EditorMode): unknown[] {
  if (editorMode !== 'frontend') return []
  const base = Date.now() - 3600_000
  const messages: Msg[] = [
    { id: 1, role: 'user', content: '我要做一个武汉分行项目管理系统，覆盖项目管理与需求回检。', createdAt: base + 1000 },
    { id: 2, role: 'assistant', content: '已开始分析需求。请先确认系统需要覆盖的业务环节、回检审核是否需要整改复检，以及涉及的角色。', createdAt: base + 2000 }
  ]
  return [
    {
      id: 'session-design-clarify',
      title: '应用设计会话',
      editorMode: 'frontend',
      threadId: 'thread-design-clarify',
      workspaceRoot,
      messages,
      createdAt: base,
      updatedAt: base + 2000
    }
  ]
}

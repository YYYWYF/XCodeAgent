// 武汉分行项目管理系统 · 新建应用会话历史（空，从零开始主旅程）。
import type { EditorMode } from '../../src/renderer/src/typings'

// 新建应用无历史会话；走完主旅程后由 mock 运行时落盘。
export function mockChatSessions(_workspaceRoot: string, _editorMode: EditorMode): unknown[] {
  return []
}

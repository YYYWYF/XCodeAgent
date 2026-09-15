// 武汉分行需求回检系统 · v1.3 当前迭代的历史会话。
// 不再手写静态剧本：直接复用交互演示的同一组 mock 剧本，按版本终态无头回放生成
// 全阶段历史（见 mock/scripts/historyReplay.ts），保证消息形态与新建应用旅程一致。
import type { EditorMode } from '../../src/renderer/src/typings'
import type { ChatSessionRecord } from '../../src/renderer/src/service/chatSessions'
import { generateHistorySessions } from '../../src/renderer/src/mock/scripts/historyReplay'

/** 返回 v1.3 当前迭代的全阶段历史会话（进程内只回放一次，随后命中缓存）。 */
export async function mockChatSessions(
  workspaceRoot: string,
  editorMode: EditorMode
): Promise<ChatSessionRecord[]> {
  if (editorMode !== 'frontend') return []
  return generateHistorySessions(workspaceRoot)
}

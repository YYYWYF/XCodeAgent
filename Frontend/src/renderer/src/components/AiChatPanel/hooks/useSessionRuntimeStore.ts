import type { MutableRefObject, ReactNode, SetStateAction } from 'react'
import { createContext, createElement, useContext, useRef, useState } from 'react'
import { AgUiChatSession } from '../../../service/agUiAgent'
import { clearEntityDesignDraftStore } from '../components/WorkflowRunCard/EntityDesignPanels'
import type { ChatMessageSkill } from '../../../typings'
import type { AgentChatMessage } from '../types'
import {
  sessionRuntimeKeyBelongsToWorkspace,
  type SessionIdentity
} from './sessionRuntime'

export type SessionRuntimeStore = {
  agUiSessionsRef: MutableRefObject<Record<string, AgUiChatSession>>
  runningSessionsRef: MutableRefObject<Map<string, SessionIdentity>>
  clearWorkspace: (workspaceRoot: string) => Promise<void>
  draftForKey: (sessionKey: string) => string
  ensureAgent: (identity: SessionIdentity) => AgUiChatSession
  getIdentity: (sessionKey: string) => SessionIdentity | undefined
  getSessionMessages: (sessionKey: string) => AgentChatMessage[]
  messagesForKey: (sessionKey: string) => AgentChatMessage[]
  selectedSkillsForKey: (sessionKey: string) => ChatMessageSkill[]
  registerSession: (
    identity: SessionIdentity,
    messages: AgentChatMessage[],
    agent?: AgUiChatSession
  ) => void
  removeSession: (sessionKey: string) => void
  setDraftByKey: (sessionKey: string, value: string) => void
  setSelectedSkillsByKey: (sessionKey: string, value: ChatMessageSkill[]) => void
  setSessionMessages: (sessionKey: string, value: SetStateAction<AgentChatMessage[]>) => void
}

const SessionRuntimeContext = createContext<SessionRuntimeStore | undefined>(undefined)

/** 在应用入口层创建会话运行态，使隐藏工作台时 AG-UI 会话与草稿继续存活。 */
export function SessionRuntimeProvider({ children }: { children: ReactNode }): JSX.Element {
  const store = useSessionRuntimeStoreState()
  return createElement(SessionRuntimeContext.Provider, { value: store }, children)
}

/** 读取应用级会话运行态，避免在可卸载的聊天面板内重复创建 store。 */
export function useSessionRuntimeStore(): SessionRuntimeStore {
  const store = useContext(SessionRuntimeContext)
  if (!store) {
    throw new Error('useSessionRuntimeStore 必须在 SessionRuntimeProvider 内使用')
  }
  return store
}

/** 创建会话运行态的具体状态容器，仅允许由应用级 Provider 持有。 */
function useSessionRuntimeStoreState(): SessionRuntimeStore {
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [messagesBySession, setMessagesBySession] = useState<Record<string, AgentChatMessage[]>>({})
  const [selectedSkillsBySession, setSelectedSkillsBySession] = useState<
    Record<string, ChatMessageSkill[]>
  >({})
  const agUiSessionsRef = useRef<Record<string, AgUiChatSession>>({})
  const runningSessionsRef = useRef<Map<string, SessionIdentity>>(new Map())
  const identitiesRef = useRef<Record<string, SessionIdentity>>({})
  const messagesRef = useRef<Record<string, AgentChatMessage[]>>({})

  /** 更新指定会话的未发送输入草稿。 */
  const setDraftByKey = (sessionKey: string, value: string): void => {
    setDrafts((current) => ({ ...current, [sessionKey]: value }))
  }

  /** 更新指定会话草稿内的技能标签，避免跨会话串用。 */
  const setSelectedSkillsByKey = (sessionKey: string, value: ChatMessageSkill[]): void => {
    setSelectedSkillsBySession((current) => ({ ...current, [sessionKey]: value }))
  }

  /** 同步更新指定会话的消息引用和渲染状态。 */
  const setSessionMessages = (
    sessionKey: string,
    value: SetStateAction<AgentChatMessage[]>
  ): void => {
    const currentMessages = messagesRef.current[sessionKey] || []
    const nextMessages =
      typeof value === 'function'
        ? (value as (current: AgentChatMessage[]) => AgentChatMessage[])(currentMessages)
        : value
    messagesRef.current[sessionKey] = nextMessages
    setMessagesBySession((current) => ({ ...current, [sessionKey]: nextMessages }))
  }

  /** 返回指定会话的 AG-UI 客户端，不存在时按 threadId 创建。 */
  const ensureAgent = (identity: SessionIdentity): AgUiChatSession => {
    identitiesRef.current[identity.key] = identity
    return (
      agUiSessionsRef.current[identity.key] ||
      (agUiSessionsRef.current[identity.key] = new AgUiChatSession(identity.threadId))
    )
  }

  /** 注册已恢复或新建的会话身份、消息及可选 AG-UI 客户端。 */
  const registerSession = (
    identity: SessionIdentity,
    messages: AgentChatMessage[],
    agent?: AgUiChatSession
  ): void => {
    identitiesRef.current[identity.key] = identity
    if (agent) agUiSessionsRef.current[identity.key] = agent
    else ensureAgent(identity)
    setSessionMessages(identity.key, messages)
  }

  /** 从内存运行态中清理已删除会话的全部关联状态。 */
  const removeSession = (sessionKey: string): void => {
    delete agUiSessionsRef.current[sessionKey]
    delete identitiesRef.current[sessionKey]
    delete messagesRef.current[sessionKey]
    setMessagesBySession((current) => omitKey(current, sessionKey))
    setDrafts((current) => omitKey(current, sessionKey))
    setSelectedSkillsBySession((current) => omitKey(current, sessionKey))
  }

  /** 停止并清理指定工作区的全部会话运行态，确保删除项目后不残留消息或草稿。 */
  const clearWorkspace = async (workspaceRoot: string): Promise<void> => {
    const sessionKeys = new Set([
      ...Object.keys(agUiSessionsRef.current),
      ...Object.keys(identitiesRef.current),
      ...Object.keys(messagesRef.current),
      ...runningSessionsRef.current.keys()
    ])
    const workspaceKeys = [...sessionKeys].filter((sessionKey) =>
      sessionRuntimeKeyBelongsToWorkspace(sessionKey, workspaceRoot)
    )
    const activeAgents = workspaceKeys
      .map((sessionKey) => agUiSessionsRef.current[sessionKey])
      .filter((agent): agent is AgUiChatSession => Boolean(agent))

    await Promise.allSettled(activeAgents.map((agent) => agent.stop()))
    workspaceKeys.forEach((sessionKey) => {
      delete agUiSessionsRef.current[sessionKey]
      delete identitiesRef.current[sessionKey]
      delete messagesRef.current[sessionKey]
      runningSessionsRef.current.delete(sessionKey)
    })
    setMessagesBySession((current) => omitWorkspaceKeys(current, workspaceRoot))
    setDrafts((current) => omitWorkspaceKeys(current, workspaceRoot))
    setSelectedSkillsBySession((current) => omitWorkspaceKeys(current, workspaceRoot))
    // 项目删除后同时清理实体设计草稿缓存，避免残留跨会话状态。
    clearEntityDesignDraftStore(workspaceRoot)
  }

  return {
    agUiSessionsRef,
    clearWorkspace,
    draftForKey: (sessionKey) => drafts[sessionKey] || '',
    ensureAgent,
    getIdentity: (sessionKey) => identitiesRef.current[sessionKey],
    getSessionMessages: (sessionKey) => messagesRef.current[sessionKey] || [],
    messagesForKey: (sessionKey) => messagesBySession[sessionKey] || [],
    runningSessionsRef,
    selectedSkillsForKey: (sessionKey) => selectedSkillsBySession[sessionKey] || [],
    registerSession,
    removeSession,
    setDraftByKey,
    setSelectedSkillsByKey,
    setSessionMessages
  }
}

/** 返回移除指定工作区全部会话键后的新对象，覆盖已注册会话和未发送草稿。 */
function omitWorkspaceKeys<T>(record: Record<string, T>, workspaceRoot: string): Record<string, T> {
  return Object.fromEntries(
    Object.entries(record).filter(
      ([sessionKey]) => !sessionRuntimeKeyBelongsToWorkspace(sessionKey, workspaceRoot)
    )
  )
}

/** 返回移除指定键后的新对象，避免直接修改 React 状态。 */
function omitKey<T>(record: Record<string, T>, key: string): Record<string, T> {
  const next = { ...record }
  delete next[key]
  return next
}

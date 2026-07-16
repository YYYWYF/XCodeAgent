import type { MutableRefObject, SetStateAction } from 'react'
import { useRef, useState } from 'react'
import { AgUiChatSession } from '../../../service/agUiAgent'
import type { ChatMessageSkill } from '../../../typings'
import type { AgentChatMessage } from '../types'
import type { SessionIdentity } from './sessionRuntime'

type SessionRuntimeStore = {
  agUiSessionsRef: MutableRefObject<Record<string, AgUiChatSession>>
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

export function useSessionRuntimeStore(): SessionRuntimeStore {
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [messagesBySession, setMessagesBySession] = useState<Record<string, AgentChatMessage[]>>({})
  const [selectedSkillsBySession, setSelectedSkillsBySession] = useState<
    Record<string, ChatMessageSkill[]>
  >({})
  const agUiSessionsRef = useRef<Record<string, AgUiChatSession>>({})
  const identitiesRef = useRef<Record<string, SessionIdentity>>({})
  const messagesRef = useRef<Record<string, AgentChatMessage[]>>({})

  const setDraftByKey = (sessionKey: string, value: string): void => {
    setDrafts((current) => ({ ...current, [sessionKey]: value }))
  }

  /** 更新指定会话草稿内的技能标签，避免跨会话串用。 */
  const setSelectedSkillsByKey = (sessionKey: string, value: ChatMessageSkill[]): void => {
    setSelectedSkillsBySession((current) => ({ ...current, [sessionKey]: value }))
  }

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

  const ensureAgent = (identity: SessionIdentity): AgUiChatSession => {
    identitiesRef.current[identity.key] = identity
    return (
      agUiSessionsRef.current[identity.key] ||
      (agUiSessionsRef.current[identity.key] = new AgUiChatSession(identity.threadId))
    )
  }

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

  const removeSession = (sessionKey: string): void => {
    delete agUiSessionsRef.current[sessionKey]
    delete identitiesRef.current[sessionKey]
    delete messagesRef.current[sessionKey]
    setMessagesBySession((current) => omitKey(current, sessionKey))
    setDrafts((current) => omitKey(current, sessionKey))
    setSelectedSkillsBySession((current) => omitKey(current, sessionKey))
  }

  return {
    agUiSessionsRef,
    draftForKey: (sessionKey) => drafts[sessionKey] || '',
    ensureAgent,
    getIdentity: (sessionKey) => identitiesRef.current[sessionKey],
    getSessionMessages: (sessionKey) => messagesRef.current[sessionKey] || [],
    messagesForKey: (sessionKey) => messagesBySession[sessionKey] || [],
    selectedSkillsForKey: (sessionKey) => selectedSkillsBySession[sessionKey] || [],
    registerSession,
    removeSession,
    setDraftByKey,
    setSelectedSkillsByKey,
    setSessionMessages
  }
}

function omitKey<T>(record: Record<string, T>, key: string): Record<string, T> {
  const next = { ...record }
  delete next[key]
  return next
}

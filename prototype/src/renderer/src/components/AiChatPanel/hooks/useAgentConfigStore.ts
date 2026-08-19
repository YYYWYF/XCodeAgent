import { useCallback, useState } from 'react'
import {
  areAgentConfigsEqual,
  cloneAgentConfig,
  createAgentConfigSessionState,
  type AgentConfigSessionState,
  type AgentConfigState
} from '../../../agentConfig'

/** 管理智能体会话级配置，隔离生效版本、编辑草稿和等待重新生成的候选版本。 */
export function useAgentConfigStore(versionKey: string): {
  getState: (agentId: string) => AgentConfigSessionState
  updateDraft: (agentId: string, draftConfig: AgentConfigState) => void
  submitDraft: (agentId: string) => AgentConfigState | undefined
  resetDraft: (agentId: string) => void
  markGenerating: (agentId: string) => void
  markAwaitingAcceptance: (agentId: string) => void
  commitPending: (agentId: string) => void
  discardPending: (agentId: string) => void
  markError: (agentId: string, error: string) => void
} {
  const [states, setStates] = useState<Record<string, AgentConfigSessionState>>({})

  /** 生成版本隔离的配置键，避免同一智能体在不同迭代间共享草稿或生效版本。 */
  const stateKey = useCallback(
    (agentId: string): string => `${versionKey}:${agentId}`,
    [versionKey]
  )

  /** 读取智能体状态；未初始化的智能体使用新的默认配置，不共享其他智能体草稿。 */
  const getState = useCallback(
    (agentId: string): AgentConfigSessionState => {
      const key = stateKey(agentId)
      return states[key] ? states[key] : createAgentConfigSessionState()
    },
    [states, stateKey]
  )

  /** 写入面板草稿并根据是否偏离生效版本更新状态。 */
  const updateDraft = useCallback((agentId: string, draftConfig: AgentConfigState): void => {
    setStates((current) => {
      const key = stateKey(agentId)
      const previous = current[key] || createAgentConfigSessionState()
      const nextDraft = cloneAgentConfig(draftConfig)
      return {
        ...current,
        [key]: {
          ...previous,
          draftConfig: nextDraft,
          error: undefined,
          status: areAgentConfigsEqual(previous.activeConfig, nextDraft) ? 'clean' : 'draft'
        }
      }
    })
  }, [stateKey])

  /** 把当前草稿冻结为候选版本，等待左侧工作流确认并重新生成代码。 */
  const submitDraft = useCallback(
    (agentId: string): AgentConfigState | undefined => {
      const key = stateKey(agentId)
      const previous = states[key] || createAgentConfigSessionState()
      if (areAgentConfigsEqual(previous.activeConfig, previous.draftConfig)) return undefined
      const pendingConfig = cloneAgentConfig(previous.draftConfig)
      setStates((current) => ({
        ...current,
        [key]: {
          ...previous,
          pendingConfig,
          draftConfig: cloneAgentConfig(pendingConfig),
          status: 'pending_generation',
          error: undefined
        }
      }))
      return pendingConfig
    },
    [states, stateKey]
  )

  /** 撤销尚未应用的草稿或候选版本，恢复到当前生效配置。 */
  const resetDraft = useCallback((agentId: string): void => {
    setStates((current) => {
      const key = stateKey(agentId)
      const previous = current[key] || createAgentConfigSessionState()
      return {
        ...current,
        [key]: {
          ...previous,
          draftConfig: cloneAgentConfig(previous.activeConfig),
          pendingConfig: undefined,
          status: 'clean',
          error: undefined
        }
      }
    })
  }, [stateKey])

  /** 标记候选配置已经进入代码生成阶段，避免用户重复提交同一版本。 */
  const markGenerating = useCallback((agentId: string): void => {
    setStates((current) => {
      const previous = current[stateKey(agentId)]
      if (!previous) return current
      const key = stateKey(agentId)
      return { ...current, [key]: { ...previous, status: 'generating', error: undefined } }
    })
  }, [stateKey])

  /** 标记候选代码已经生成并等待用户逐文件验收。 */
  const markAwaitingAcceptance = useCallback((agentId: string): void => {
    setStates((current) => {
      const key = stateKey(agentId)
      const previous = current[key]
      if (!previous) return current
      return { ...current, [key]: { ...previous, status: 'awaiting_acceptance' } }
    })
  }, [stateKey])

  /** 在工作流验收完成后把候选版本提升为当前生效配置。 */
  const commitPending = useCallback((agentId: string): void => {
    setStates((current) => {
      const key = stateKey(agentId)
      const previous = current[key]
      if (!previous?.pendingConfig) return current
      const activeConfig = cloneAgentConfig(previous.pendingConfig)
      return {
        ...current,
        [key]: {
          activeConfig,
          draftConfig: cloneAgentConfig(activeConfig),
          hasAppliedRevision: true,
          status: 'clean',
          error: undefined
        }
      }
    })
  }, [stateKey])

  /** 在候选工作流取消或失败时丢弃候选，保留原生效配置。 */
  const discardPending = useCallback((agentId: string): void => {
    setStates((current) => {
      const key = stateKey(agentId)
      const previous = current[key]
      if (!previous) return current
      return {
        ...current,
        [key]: {
          ...previous,
          draftConfig: cloneAgentConfig(previous.activeConfig),
          pendingConfig: undefined,
          status: 'clean',
          error: undefined
        }
      }
    })
  }, [stateKey])

  /** 保存候选生成失败信息，同时保留草稿供用户修改后重新提交。 */
  const markError = useCallback((agentId: string, error: string): void => {
    setStates((current) => {
      const key = stateKey(agentId)
      const previous = current[key]
      if (!previous) return current
      return { ...current, [key]: { ...previous, status: 'error', error } }
    })
  }, [stateKey])

  return {
    getState,
    updateDraft,
    submitDraft,
    resetDraft,
    markGenerating,
    markAwaitingAcceptance,
    commitPending,
    discardPending,
    markError
  }
}

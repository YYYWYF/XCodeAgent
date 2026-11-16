import { useEffect, useRef, useState } from 'react'
import { message } from 'antd'
import { BuiltinPatchResponseError, runBuiltinPatch, type BuiltinPatchStage } from '../../service/builtinAuthorization'
import { builtinAuthorizationVisible, notifyBuiltinAuthorizationChanged } from './builtinAuthorizationEvents'

type Action = 'run_patch' | 'retry' | 'refresh_patch'
type Feedback = { progress?: { stage: BuiltinPatchStage; message: string }; error?: string }
const requests = new Set<string>()

/** 共用显式动作和请求反馈；成功回执只触发读回，不作为任务快照。 */
export function useBuiltinAuthorizationActions(workspace?: string, disabled = false) {
  const [feedback, setFeedback] = useState<{ workspace: string; value: Feedback }>()
  const scope = useRef(workspace)
  scope.current = workspace
  const owner = useRef<object>()
  useEffect(() => {
    setFeedback(undefined)
    return () => { owner.current = undefined }
  }, [workspace])

  /** 串行提交同一工作区的动作，避免双击或多个入口同时发送。 */
  async function execute(action: Action): Promise<void> {
    if (!workspace || disabled || !builtinAuthorizationVisible() || requests.has(workspace)) return
    const request = {}
    owner.current = request
    requests.add(workspace)
    /** 只向当前挂载的请求所有者显示进度或错误。 */
    const isCurrent = () => owner.current === request && scope.current === workspace
    /** 写入本地提示，不从动作结果推断 Schema 或 Seed 阶段。 */
    function update(value: Feedback): void {
      if (isCurrent()) setFeedback({ workspace: workspace!, value })
    }

    update({ progress: { stage: action === 'run_patch' ? 'schema' : 'query',
      message: action === 'run_patch' ? '正在检查权限表…' : '正在查询原操作…' } })
    try {
      const result = await runBuiltinPatch(workspace, action, (stage, text) => update({ progress: { stage, message: text } }))
      if (action === 'retry' && result && isCurrent() && builtinAuthorizationVisible()) {
        message.info(['pending', 'running', 'waiting'].includes(result.status)
          ? '已恢复权限初始化，将自动查询并核验结果。' : result.message)
      }
      // 未落盘的业务提示仍反馈给当前操作者，阶段和成功状态只由后续 get 更新。
      update(result && action !== 'retry' && ['failed', 'needs_attention'].includes(result.status)
        ? { error: result.message } : {})
    } catch (error) {
      update({ error: error instanceof BuiltinPatchResponseError ? error.message
        : '连接中断，无法确认本次操作结果。请查询原操作。' })
    } finally {
      // 即使抽屉已关闭或回执丢失，其他入口也可以读取已存在的后台任务。
      notifyBuiltinAuthorizationChanged(workspace)
      requests.delete(workspace)
    }
  }

  const current = feedback?.workspace === workspace ? feedback?.value : undefined
  return { ...current, busy: Boolean(current?.progress), execute }
}

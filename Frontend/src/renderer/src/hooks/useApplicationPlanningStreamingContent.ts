import { useEffect, useState } from 'react'
import type { ApplicationPlanningRuntime } from '../service/applicationPlanningRuntime'

/** 可见视图只订阅 Runtime 的临时正文，卸载不会停止后台规划。 */
export function useApplicationPlanningStreamingContent(runtime?: ApplicationPlanningRuntime): string {
  const [snapshot, setSnapshot] = useState<{ runtime?: ApplicationPlanningRuntime; content: string }>({ content: '' })
  useEffect(() => {
    if (!runtime) return
    return runtime.subscribeStreamingContent((content) => setSnapshot({ runtime, content }))
  }, [runtime])
  return snapshot.runtime === runtime ? snapshot.content : ''
}

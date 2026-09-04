import { useEffect, useRef } from 'react'

type UseCountedRequestTriggerParams = {
  /** 自增请求计数（每次外部发起 +1）；缺省、0 或重复值不触发。 */
  request: number | undefined
  /** 请求到达时是否具备执行条件；不满足时只消费计数不执行。 */
  available: boolean
  /** 满足条件时执行的打开动作。 */
  onOpen: () => void
}

/**
 * 消费“顶部阶段条发起进入请求”这类自增计数触发器：每个计数值只执行一次，
 * 与具体弹框解耦。阶段准入门（测试/审查/开发/项目规划）共用同一模式，
 * 各自只声明 available 判定与要打开的弹框。
 */
export function useCountedRequestTrigger({
  request,
  available,
  onOpen
}: UseCountedRequestTriggerParams): void {
  const handledRequestRef = useRef(0)
  useEffect(() => {
    if (!request || request <= handledRequestRef.current) return
    handledRequestRef.current = request
    if (!available) return
    onOpen()
    // onOpen 取每次渲染的最新闭包：调用方用 setState 包装，无需进入依赖。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [available, request])
}

import { useEffect, useRef, useState } from 'react'

// 把百分比限制在进度条可展示的有效范围内。
function clampPercent(value: number): number {
  return Math.round(Math.max(0, Math.min(100, value)))
}

const PROGRESS_DELAYS = [650, 900, 1800, 750, 1200, 2400] as const

// 根据当前位置和节奏拍点返回整数步长，停顿拍点保持当前进度不变。
function progressStep(value: number, pulseIndex: number): number {
  if (pulseIndex === 2 || pulseIndex === 5) return 0
  if (value < 40) return 3
  if (value < 76) return 2
  return 1
}

// 在真实阶段进度之间平滑推进，并用活动信号避免长耗时阶段看起来完全停滞。
export function useProgressivePercent(target: number, ceiling: number, activityKey = 0): number {
  const clampedTarget = clampPercent(target)
  const safeTarget = clampedTarget >= 100 ? 100 : Math.min(98, clampedTarget)
  const safeCeiling = safeTarget >= 100
    ? 100
    : Math.min(98, Math.max(safeTarget, clampPercent(ceiling)))
  const [displayed, setDisplayed] = useState(safeTarget)
  const lastActivityRef = useRef(activityKey)
  const lastActivityAtRef = useRef(0)

  // 后端报告新阶段时只轻推当前进度，避免直接跳到阶段锚点显得机械。
  useEffect(() => {
    setDisplayed((current) => {
      if (safeTarget >= 100) return 100
      if (current >= safeTarget) return current
      return Math.min(safeCeiling, current + 2)
    })
  }, [safeCeiling, safeTarget])

  // 收到新的模型输出或工具活动时至多轻推 1%，不打断自然的停顿节奏。
  useEffect(() => {
    if (lastActivityRef.current === activityKey) return
    lastActivityRef.current = activityKey
    const now = Date.now()
    if (now - lastActivityAtRef.current < 1800) return
    lastActivityAtRef.current = now
    setDisplayed((current) => Math.min(safeCeiling, current + 1))
  }, [activityKey, safeCeiling, safeTarget])

  // 使用不同等待时长循环推进，穿插不增长的拍点，形成“缓慢增长—停顿—继续增长”的节奏。
  useEffect(() => {
    if (safeTarget >= 100) return undefined
    let timer: number | undefined
    let pulseIndex = 0

    // 按当前拍点安排下一次增长或停顿，并在结束后继续循环。
    const scheduleNext = (): void => {
      const delay = PROGRESS_DELAYS[pulseIndex]
      timer = window.setTimeout(() => {
        setDisplayed((current) => (
          Math.min(safeCeiling, current + progressStep(current, pulseIndex))
        ))
        pulseIndex = (pulseIndex + 1) % PROGRESS_DELAYS.length
        scheduleNext()
      }, delay)
    }

    scheduleNext()
    return () => {
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [safeCeiling, safeTarget])

  return Math.round(displayed)
}

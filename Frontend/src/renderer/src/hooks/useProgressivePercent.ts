import { useEffect, useRef, useState } from 'react'

// 把百分比限制在进度条可展示的有效范围内。
function clampPercent(value: number): number {
  return Math.max(0, Math.min(100, value))
}

// 在真实阶段进度之间平滑推进，并用活动信号避免长耗时阶段看起来完全停滞。
export function useProgressivePercent(target: number, ceiling: number, activityKey = 0): number {
  const safeTarget = clampPercent(target)
  const safeCeiling = Math.max(safeTarget, clampPercent(ceiling))
  const [displayed, setDisplayed] = useState(safeTarget)
  const lastActivityRef = useRef(activityKey)
  const lastActivityAtRef = useRef(0)

  // 后端报告新阶段时立即追上真实锚点，但不允许倒退造成视觉跳变。
  useEffect(() => {
    setDisplayed((current) => safeTarget >= 100 ? 100 : Math.max(current, safeTarget))
  }, [safeTarget])

  // 收到新的模型输出或工具活动时追加一个受阶段上限约束的小幅进度。
  useEffect(() => {
    if (lastActivityRef.current === activityKey) return
    lastActivityRef.current = activityKey
    const now = Date.now()
    if (now - lastActivityAtRef.current < 1200) return
    lastActivityAtRef.current = now
    setDisplayed((current) => Math.min(safeCeiling, Math.max(current, safeTarget) + 0.9))
  }, [activityKey, safeCeiling, safeTarget])

  // 长耗时阶段采用越接近上限越慢的推进速度，避免固定百分比长时间卡住。
  useEffect(() => {
    if (safeTarget >= 100) return undefined
    const timer = window.setInterval(() => {
      setDisplayed((current) => {
        if (current >= safeCeiling) return current
        const remaining = safeCeiling - current
        const increment = Math.max(0.18, Math.min(0.85, remaining * 0.04))
        return Math.min(safeCeiling, Math.max(current, safeTarget) + increment)
      })
    }, 900)
    return () => window.clearInterval(timer)
  }, [safeCeiling, safeTarget])

  return Math.round(displayed)
}

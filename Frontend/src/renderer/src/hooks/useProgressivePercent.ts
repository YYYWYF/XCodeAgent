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
    setDisplayed((current) => Math.min(safeCeiling, Math.max(current, safeTarget) + 0.4))
  }, [activityKey, safeCeiling, safeTarget])

  // 等待后端新阶段时按稳定节奏增加 0.1%，让总百分比本身持续产生可见变化。
  useEffect(() => {
    if (safeTarget >= 100) return undefined
    const timer = window.setInterval(() => {
      setDisplayed((current) => {
        const baseline = Math.max(current, safeTarget)
        return Math.min(safeCeiling, baseline + 0.1)
      })
    }, 500)
    return () => window.clearInterval(timer)
  }, [safeCeiling, safeTarget])

  return Math.round(displayed * 10) / 10
}

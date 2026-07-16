import { useEffect, useState } from 'react'

const COMPACT_WORKBENCH_QUERY = '(max-width: 900px)'

/** 监听工作台窄屏断点，让需要交互配合的布局与 CSS 保持同步。 */
export function useCompactWorkbench(): boolean {
  const [compact, setCompact] = useState(() => window.matchMedia(COMPACT_WORKBENCH_QUERY).matches)

  useEffect(() => {
    const mediaQuery = window.matchMedia(COMPACT_WORKBENCH_QUERY)
    /** 将系统媒体查询变化同步到 React 布局状态。 */
    const handleChange = (event: MediaQueryListEvent): void => setCompact(event.matches)

    setCompact(mediaQuery.matches)
    mediaQuery.addEventListener('change', handleChange)
    return () => mediaQuery.removeEventListener('change', handleChange)
  }, [])

  return compact
}

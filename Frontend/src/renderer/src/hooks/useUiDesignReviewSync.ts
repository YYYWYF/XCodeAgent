import { useContext, useEffect, useRef, useState } from 'react'
import type { DesignPreviewPage } from '../components/DesignRenderer/projectPreview'
import { UiReadVisibility } from './uiReadVisibility'
import { designPagesGenerating } from './uiDesignPageSource'

type ReviewPage = DesignPreviewPage & { code_sha256?: string }

/** 比较正式页面的状态和资产身份，排除预览 URL、内联代码等运行时字段。 */
function reviewKey(pages: ReviewPage[]): string {
  return JSON.stringify(
    pages.map((page) => {
      const asset = page.design_asset
      return [
        page.pageId,
        page.status,
        page.code_sha256,
        asset && [
          asset.kind,
          asset.draft,
          asset.revision,
          asset.sourceDigest,
          asset.buildDigest,
          asset.pageIds
        ]
      ]
    })
  )
}

/** 后台完成后显式刷新审阅门；未收到同一版本的工作流回执前禁止确认。 */
export function useUiDesignReviewSync(
  workspaceRoot: string | undefined,
  sourcePages: ReviewPage[],
  refresh: () => void
): boolean {
  const visible = useContext(UiReadVisibility)
  const sourceKey = reviewKey(sourcePages)
  const current = useRef({ sourceKey, refresh })
  current.current = { sourceKey, refresh }
  const [disk, setDisk] = useState<{ workspace: string; key: string }>()
  const pollRef = useRef<() => Promise<void>>()
  const canRead = Boolean(workspaceRoot && window.devAgentStudio?.workspace?.readUiDesigns)

  useEffect(() => {
    if (!workspaceRoot || !canRead || !visible) return
    let cancelled = false
    let reading = false
    /** 串行读取；刷新被正在进行的 run 拦下时，下轮继续尝试，不丢失完成信号。 */
    const poll = async (): Promise<void> => {
      if (reading) return
      reading = true
      try {
        const result = await window.devAgentStudio?.workspace?.readUiDesigns({ workspaceRoot })
        if (cancelled || !result?.uiDesigns) return
        const pages = (result.uiDesigns as { pages?: ReviewPage[] }).pages
        if (!Array.isArray(pages)) return
        const key = reviewKey(pages)
        setDisk((previous) =>
          previous?.workspace === workspaceRoot && previous.key === key
            ? previous
            : { workspace: workspaceRoot, key }
        )
        // 磁盘 ready 只允许展示原型；必须通过原 AG-UI refresh 把新身份写入 Graph。
        if (key !== current.current.sourceKey && !designPagesGenerating(pages)) {
          current.current.refresh()
        }
      } catch {
        // 读取失败保持未同步状态，等待下一轮或用户手动刷新，不能批准未知版本。
      } finally {
        reading = false
      }
    }
    pollRef.current = poll
    void poll()
    const timer = window.setInterval(() => void poll(), 2000)
    return () => {
      cancelled = true
      pollRef.current = undefined
      window.clearInterval(timer)
    }
  }, [workspaceRoot, canRead, visible])

  useEffect(() => {
    void pollRef.current?.()
  }, [sourceKey])

  return canRead && (disk?.workspace !== workspaceRoot || disk?.key !== sourceKey)
}

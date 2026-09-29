import { useEffect, useMemo, useState } from 'react'

/** 页面清单里本 hook 关心的字段。 */
type UiDesignPageLike = {
  pageId?: string
  status?: string
  code?: string
}

/**
 * 把磁盘上的 code / status 合进页面清单（纯函数，便于单测）。
 *
 * 只在页面**缺 code** 时补 —— 已有 code 的版本来自生成池，比磁盘新。status 以磁盘为准：
 * 它是后台生成池写下的权威事实，快照里的可能还停在 queued/generating。
 * 两者都没变化时原样返回该页对象，避免无谓的重渲染。
 */
export function mergeUiDesignPageSources<T extends UiDesignPageLike>(
  sourcePages: T[],
  diskCode: Record<string, string>,
  diskStatus: Record<string, string>
): T[] {
  return sourcePages.map((page) => {
    const pageId = String(page.pageId || '')
    if (!pageId) return page
    const code = page.code ? undefined : diskCode[pageId]
    const status = diskStatus[pageId]
    if (!code && (!status || status === page.status)) return page
    return {
      ...page,
      ...(code ? { code } : {}),
      ...(status ? { status } : {})
    }
  })
}

/**
 * 给 UI 设计稿页面清单补上磁盘上的 `code`，让「查看设计稿」与右侧预览真正渲染得出来。
 *
 * **为什么需要**：正式 manifest 只存 `code_path` 不存源码（运行时数据不入库），
 * workflow 快照里的页面同样没有 code。于是**重新打开工作区**后，按钮虽然可按
 * `page.code` 判可用性而禁用，即便放开了也没有东西可渲染 —— 本轮新生成的和从上一轮
 * 继承来的一视同仁。
 *
 * 主进程的 `workspace:read-ui-designs` IPC 会按 `page_key` 把源码读回来（见
 * `src/main/uiDesignPageCode.ts`）。这里轮询它，把 code 与最新 status 合进传入的清单。
 *
 * 清单本身仍以调用方给的为准（顺序、名称、模板等信息不丢）；只在页面缺 code 时补，
 * 已有 code 的不覆盖 —— 生成池刚写完的版本比磁盘新。
 */
export function useUiDesignPagesWithCode<T extends UiDesignPageLike>(
  workspaceRoot: string | undefined,
  sourcePages: T[] | undefined
): T[] {
  // 磁盘上的源码与状态：按 pageId 索引。
  const [diskCode, setDiskCode] = useState<Record<string, string>>({})
  const [diskStatus, setDiskStatus] = useState<Record<string, string>>({})

  useEffect(() => {
    if (!workspaceRoot) return
    let cancelled = false
    const poll = async (): Promise<void> => {
      try {
        const result = await window.devAgentStudio?.workspace?.readUiDesigns({ workspaceRoot })
        if (cancelled || !result?.uiDesigns) return
        const pages = (result.uiDesigns as { pages?: unknown }).pages
        if (!Array.isArray(pages)) return
        const codes: Record<string, string> = {}
        const statuses: Record<string, string> = {}
        for (const page of pages) {
          if (!page || typeof page !== 'object') continue
          const pageId = String((page as UiDesignPageLike).pageId || '')
          if (!pageId) continue
          const code = String((page as UiDesignPageLike).code || '')
          if (code) codes[pageId] = code
          const status = String((page as UiDesignPageLike).status || '')
          if (status) statuses[pageId] = status
        }
        if (!cancelled) {
          setDiskCode(codes)
          setDiskStatus(statuses)
        }
      } catch {
        // 读取失败不阻塞，下次轮询重试。
      }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 2000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [workspaceRoot])

  return useMemo(
    () => mergeUiDesignPageSources(sourcePages ?? [], diskCode, diskStatus),
    [sourcePages, diskCode, diskStatus]
  )
}

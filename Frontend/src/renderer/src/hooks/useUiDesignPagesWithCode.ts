import { useContext, useEffect, useMemo, useRef, useState } from 'react'
import { UiReadVisibility } from './uiReadVisibility'
import {
  designPagesGenerating,
  EMPTY_DESIGN_SOURCE,
  subscribeDesignSource,
  type DesignSourceSnapshot,
  type UiDesignPageSource
} from './uiDesignPageSource'

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
  const merged = sourcePages.map((page) => {
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
  return merged.every((page, index) => page === sourcePages[index]) ? sourcePages : merged
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
  const visible = useContext(UiReadVisibility)
  const sourceRef = useRef<UiDesignPageSource>()
  const [disk, setDisk] = useState<{ workspace?: string; value: DesignSourceSnapshot }>({
    value: EMPTY_DESIGN_SOURCE
  })
  // 只对页面身份/生成状态变化校准磁盘，不把大段 TSX 序列化为依赖键。
  const sourceKey = JSON.stringify((sourcePages || []).map((page) => [page.pageId, page.status]))
  const generating = designPagesGenerating(sourcePages || [])

  useEffect(() => {
    if (!workspaceRoot || !visible) return
    const subscription = subscribeDesignSource(workspaceRoot, (value) => {
      setDisk((current) =>
        current.workspace === workspaceRoot && current.value === value
          ? current
          : { workspace: workspaceRoot, value }
      )
    })
    sourceRef.current = subscription.source
    return () => {
      sourceRef.current = undefined
      subscription.release()
    }
  }, [workspaceRoot, visible])

  useEffect(() => {
    sourceRef.current?.updateSource(sourceKey, generating)
  }, [workspaceRoot, visible, sourceKey, generating])

  const snapshot = disk.workspace === workspaceRoot ? disk.value : EMPTY_DESIGN_SOURCE
  return useMemo(
    () => mergeUiDesignPageSources(sourcePages ?? [], snapshot.codes, snapshot.statuses),
    [sourcePages, snapshot]
  )
}

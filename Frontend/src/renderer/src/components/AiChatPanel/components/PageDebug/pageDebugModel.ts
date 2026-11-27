import {
  previewServiceAttemptId,
  type PreviewRuntimePayload
} from '../../../../service/previewRuntime'
import type { DevelopmentArtifacts, DevelopmentPlanningPageOption } from '../../../../typings'
import { composePreviewUrl } from '../../../../utils/previewUrl'

/** 调试已完成页面时，按模板的短横线路由打开，保留目录里的名称与页面身份。 */
export function debugPageOptions(
  pages: DevelopmentPlanningPageOption[],
  artifacts?: DevelopmentArtifacts
): DevelopmentPlanningPageOption[] {
  return pages.filter((page) => {
    const progress = artifacts?.pages[page.pageId]
    return (
      page.changeStatus !== 'deleted' &&
      progress?.initialDevelopmentStatus === 'completed' &&
      (progress.changeStatus ?? 'unchanged') === 'unchanged'
    )
  }).map((page) => ({ ...page, path: `/page/${page.pageId.replace(/_/g, '-')}` }))
}

/** 使用当前前端轮次的回环入口，拒绝导航到其他 origin。 */
export function debugPageUrl(snapshot?: PreviewRuntimePayload, route = ''): string {
  const runtime = snapshot?.runtime
  const gateway = snapshot?.pageDebug
  const origin = gateway?.origin
  if (
    runtime?.frontend.status !== 'running' ||
    !route ||
    !gateway ||
    !origin ||
    !/^http:\/\/127\.0\.0\.1:[1-9]\d{0,4}$/.test(origin) ||
    gateway.frontendAttemptId !== previewServiceAttemptId(runtime, 'frontend')
  )
    return ''
  const url = composePreviewUrl(origin, route)
  return url && new URL(url).origin === origin ? url : ''
}

/** 源码定位仅允许当前应用的 frontend/src，拒绝路径穿越及非源码文件。 */
export function debugPageSourcePath(sourcePath?: string): string {
  if (!sourcePath?.startsWith('/src/') || /[\p{Cc}\\:?#]/u.test(sourcePath)) return ''
  const segments = sourcePath.slice('/src/'.length).split('/')
  if (segments.some((segment) => !segment || segment === '.' || segment === '..')) return ''
  return /\.[cm]?[jt]sx?$/i.test(sourcePath) ? `frontend${sourcePath}` : ''
}

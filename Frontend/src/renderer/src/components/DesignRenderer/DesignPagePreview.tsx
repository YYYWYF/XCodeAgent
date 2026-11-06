import { useEffect, useRef, useState, type ReactElement } from 'react'
import { Button, Spin, Typography } from 'antd'
import DesignRenderer from './DesignRenderer'
import { cx } from '../../utils'
import {
  isAgentProjectPage,
  projectPreviewMessageMatches,
  projectPreviewUrl,
  type DesignPreviewPage,
  type AgentUiProjectPreview
} from './projectPreview'
import './DesignPagePreview.less'

type Props = {
  page: DesignPreviewPage
  workspaceRoot?: string
  fullscreen?: boolean
  onOpenPreview?: (pageId: string) => void
}

type PreviewFrame = AgentUiProjectPreview & { key: string; frameUrl: string }

/** 当前工程在原画布内运行；官方表单可触发提交事件，原生导航仍由响应 CSP 禁止。 */
function AgentUiProjectFrame({
  page,
  workspaceRoot,
  fullscreen,
  onOpenPreview
}: Props): ReactElement {
  const frames = useRef(new Map<string, HTMLIFrameElement>())
  const preview = page.project_preview
  const url = projectPreviewUrl(preview, {
    workspace: workspaceRoot || '',
    pageId: page.pageId || '',
    revision: page.design_asset?.revision || '',
    sourceDigest: page.design_asset?.sourceDigest || '',
    buildDigest: page.design_asset?.buildDigest || ''
  })
  const [result, setResult] = useState<{ url?: string; error?: string }>({})
  const [displayed, setDisplayed] = useState<PreviewFrame>()
  const ready = result.url === url
  const launchId = preview?.launchId
  const revision = preview?.revision
  const pageId = preview?.pageId
  const sourceDigest = preview?.sourceDigest
  const buildDigest = preview?.buildDigest
  const current: PreviewFrame | undefined = url && preview
    ? { ...preview, key: `${preview.workspace}:${preview.launchId}`, frameUrl: url }
    : undefined
  // 仅保留同一页面或同一存活文档；同工程切页只改 hash，新投影迟到或失败时提示旧版仍在展示。
  const previous = displayed && displayed.workspace === workspaceRoot &&
    (displayed.pageId === page.pageId || displayed.key === current?.key) &&
    page.design_asset ? displayed : undefined
  const retained = previous && previous.key !== current?.key ? previous : undefined
  const visibleKey = retained?.key || current?.key
  const rendered = [retained, current].filter((item): item is PreviewFrame => Boolean(item))

  useEffect(() => {
    if (!url || !launchId || !revision || !pageId || !sourceDigest || !buildDigest || !workspaceRoot) return
    // 中间快照短暂缺少 URL 后，同一已验证文档不会重新加载，不能再等待首次就绪回执。
    let finished = displayed?.frameUrl === url && displayed.workspace === workspaceRoot &&
      displayed.revision === revision && displayed.sourceDigest === sourceDigest &&
      displayed.buildDigest === buildDigest
    // iframe 已在同一文档内切页，旧页就绪回执必须被新 pageId 拒绝。
    const receive = (event: MessageEvent): void => {
      if (
        event.source !== frames.current.get(`${workspaceRoot}:${launchId}`)?.contentWindow ||
        event.origin !== new URL(url).origin ||
        !projectPreviewMessageMatches(event.data, { launchId, revision, pageId })
      )
        return
      finished = true
      if (event.data.type === 'agent-ui-design-ready') {
        // 新文档真实就绪后再替换已展示文档，同一 launch 的切页只更新 hash。
        setDisplayed({ kind: 'agent-ui-project', source: 'official-mock', workspace: workspaceRoot,
          pageId, revision, launchId, sourceDigest, buildDigest,
          url: url.split('#')[0], key: `${workspaceRoot}:${launchId}`, frameUrl: url })
      }
      setResult({
        url,
        error:
          event.data.type === 'agent-ui-design-error'
            ? '设计页面运行失败，请检查本轮构建或重新打开预览。'
            : undefined
      })
    }
    window.addEventListener('message', receive)
    const timer = window.setTimeout(() => {
      if (!finished) setResult({ url, error: '设计预览未就绪或连接已关闭，请重新打开预览。' })
    }, 20000)
    return () => {
      window.removeEventListener('message', receive)
      window.clearTimeout(timer)
    }
  }, [url, launchId, revision, pageId, workspaceRoot, sourceDigest, buildDigest, displayed])

  const error = !url
    ? page.preview_error || (retained ? undefined : '当前设计预览不可用，请重新打开预览。')
    : ready
      ? result.error
      : undefined
  return (
    <section className={cx('agent-project-preview', fullscreen && 'is-fullscreen')}>
      {rendered.map((item) => (
        <iframe
          key={item.key}
          ref={(element) => {
            if (element) frames.current.set(item.key, element)
            else frames.current.delete(item.key)
          }}
          src={item.frameUrl}
          className={cx(item.key !== visibleKey && 'agent-project-preview-pending')}
          aria-hidden={item.key !== visibleKey || undefined}
          tabIndex={item.key !== visibleKey ? -1 : undefined}
          title={`设计稿-${page.name || page.pageId}`}
          sandbox="allow-scripts allow-same-origin allow-forms"
          referrerPolicy="no-referrer"
          allow="camera 'none'; microphone 'none'; geolocation 'none'; clipboard-read 'none'; clipboard-write 'none'"
        />
      ))}
      {(!url || !ready || error) && (
        <div className={cx('agent-project-preview-status', previous && 'is-retaining-preview')} role={error ? 'alert' : 'status'}>
          {error ? (
            <>
              <Typography.Text>{previous ? `${error} 当前保留上一版画面。` : error}</Typography.Text>
              {page.design_asset && onOpenPreview && (
                <Button onClick={() => onOpenPreview(page.pageId || '')}>打开预览</Button>
              )}
            </>
          ) : (
            <>
              <Spin />
              <Typography.Text>{retained ? '正在准备新预览，暂时展示上一版…' : '正在加载官方模拟预览…'}</Typography.Text>
            </>
          )}
        </div>
      )}
    </section>
  )
}

/** 根据当前产物类型选择预览器，Agent 页面不执行旧 TSX 或访问本地源码路径。 */
export default function DesignPagePreview(props: Props): ReactElement {
  if (isAgentProjectPage(props.page)) return <AgentUiProjectFrame {...props} />
  if (props.page.code)
    return (
      <DesignRenderer
        code={props.page.code}
        fullscreen={props.fullscreen}
        title={`设计稿-${props.page.name || props.page.pageId}`}
      />
    )
  return <Typography.Text type="secondary">本页尚未生成设计稿。</Typography.Text>
}

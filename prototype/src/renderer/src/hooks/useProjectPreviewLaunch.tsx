import { useEffect, useRef, useState } from 'react'
import { LoadingOutlined } from '@ant-design/icons'
import { notification } from 'antd'
import { startProjectLaunch, stopProjectPreview } from '../service/projectLaunch'
import { cx, previewOrigin } from '../utils'

type UseProjectPreviewLaunchParams = {
  /** 应用唯一标识，用于启动通知的去重 key。 */
  applicationId: string
  /** 应用工作区路径；为空时不启动预览。 */
  workspaceRoot: string
  /** 备用父目录：workspaceRoot 缺失时按此路径启动。 */
  projectParentPath: string
}

type UseProjectPreviewLaunchResult = {
  /** 预览服务基地址（origin），启动成功后供预览面板拼接路由。 */
  previewBaseUrl: string
  /** 启动失败的错误文案；成功时为空。 */
  previewLaunchError: string
}

/**
 * 进入工作台时自动异步启动项目预览：安装依赖并拉起开发服务器，
 * 以底部通知反馈启动中/成功/失败。应用在多个工作区之间切换时，
 * 通过 runId 与活动工作区标记避免旧启动结果覆盖新应用状态。
 */
export function useProjectPreviewLaunch({
  applicationId,
  workspaceRoot,
  projectParentPath
}: UseProjectPreviewLaunchParams): UseProjectPreviewLaunchResult {
  const [previewBaseUrl, setPreviewBaseUrl] = useState('')
  const [previewLaunchError, setPreviewLaunchError] = useState('')
  const launchedWorkspaceRef = useRef<string>()
  const activeLaunchWorkspaceRef = useRef('')
  const launchRunIdRef = useRef(0)
  const launchCleanupPendingRef = useRef(false)
  const launchCleanupTimerRef = useRef<number>()

  useEffect(() => {
    const workspacePath = workspaceRoot || projectParentPath || ''
    if (launchCleanupTimerRef.current !== undefined) {
      window.clearTimeout(launchCleanupTimerRef.current)
      launchCleanupTimerRef.current = undefined
    }
    launchCleanupPendingRef.current = false
    if (!workspacePath) {
      activeLaunchWorkspaceRef.current = ''
      return
    }
    activeLaunchWorkspaceRef.current = workspacePath
    if (launchedWorkspaceRef.current === workspacePath) {
      const existingLaunchRunId = launchRunIdRef.current
      return () => {
        launchCleanupPendingRef.current = true
        launchCleanupTimerRef.current = window.setTimeout(() => {
          if (
            launchRunIdRef.current === existingLaunchRunId &&
            activeLaunchWorkspaceRef.current === workspacePath
          ) {
            activeLaunchWorkspaceRef.current = ''
          }
        }, 0)
      }
    }
    const launchRunId = launchRunIdRef.current + 1
    launchRunIdRef.current = launchRunId
    launchedWorkspaceRef.current = workspacePath

    const loadingKey = `project-launch-${applicationId}-${launchRunId}`
    notification.open({
      key: loadingKey,
      message: '项目正在启动中',
      description: '正在安装依赖并启动开发服务器，请稍候...',
      placement: 'bottomRight',
      duration: null,
      icon: <LoadingOutlined />,
      className: cx('project-launch-loading')
    })

    startProjectLaunch(workspacePath)
      .then((result) => {
        const launchStillCurrent =
          launchRunIdRef.current === launchRunId &&
          activeLaunchWorkspaceRef.current === workspacePath &&
          !launchCleanupPendingRef.current
        notification.close(loadingKey)
        if (!launchStillCurrent) {
          if (result.status === 'running') {
            void stopProjectPreview(workspacePath).finally(() => {
              void window.xcodeAgent?.projectPreview?.unregisterWorkspace({
                workspaceRoot: workspacePath
              })
            })
          }
          return
        }
        if (result.status === 'running' && result.preview_url) {
          void window.xcodeAgent?.projectPreview?.registerWorkspace({
            workspaceRoot: workspacePath
          })
          setPreviewBaseUrl(previewOrigin(result.preview_url))
          setPreviewLaunchError('')
          notification.success({
            message: '项目预览已启动',
            description: '可在预览面板中查看效果',
            placement: 'bottomRight',
            duration: 3
          })
        } else {
          const errorMsg = result.message || '未知错误'
          setPreviewBaseUrl('')
          setPreviewLaunchError(errorMsg)
          notification.warning({
            message: '项目预览启动失败',
            description: `${errorMsg}，可在预览区查看详情`,
            placement: 'bottomRight',
            duration: 3
          })
        }
      })
      .catch((err) => {
        notification.close(loadingKey)
        const launchStillCurrent =
          launchRunIdRef.current === launchRunId &&
          activeLaunchWorkspaceRef.current === workspacePath &&
          !launchCleanupPendingRef.current
        if (!launchStillCurrent) return
        const errorMsg = err instanceof Error ? err.message : '网络请求失败'
        setPreviewBaseUrl('')
        setPreviewLaunchError(errorMsg)
      })
    return () => {
      launchCleanupPendingRef.current = true
      launchCleanupTimerRef.current = window.setTimeout(() => {
        if (
          launchRunIdRef.current === launchRunId &&
          activeLaunchWorkspaceRef.current === workspacePath
        ) {
          activeLaunchWorkspaceRef.current = ''
        }
      }, 0)
    }
  }, [applicationId, projectParentPath, workspaceRoot])

  return { previewBaseUrl, previewLaunchError }
}

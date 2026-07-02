import { ReloadOutlined, SaveOutlined, UndoOutlined } from '@ant-design/icons'
import { Alert, Button, Empty, Input, Modal, Spin, Switch, Typography, message } from 'antd'
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useApi } from '../../context/ApiContext'
import type {
  XcodeAgentMarkdownFileContent,
  XcodeAgentMarkdownFileName,
  XcodeAgentMarkdownFileSummary
} from '../../../../shared/xcodeagent'
import './AgentsPage.less'

const formatFileSize = (size: number): string => {
  if (size < 1024) {
    return `${size} B`
  }

  return `${(size / 1024).toFixed(2)} kB`
}

const formatRelativeTime = (value: string): string => {
  const date = new Date(value)

  if (Number.isNaN(date.getTime())) {
    return value
  }

  const diffMs = Date.now() - date.getTime()
  const diffMinutes = Math.max(0, Math.floor(diffMs / 60000))

  if (diffMinutes < 1) {
    return '刚刚'
  }

  if (diffMinutes < 60) {
    return `${diffMinutes} 分钟前`
  }

  const diffHours = Math.floor(diffMinutes / 60)

  if (diffHours < 24) {
    return `${diffHours} 小时前`
  }

  return `${Math.floor(diffHours / 24)} 天前`
}

const confirmDiscardChanges = (): Promise<boolean> =>
  new Promise((resolve) => {
    Modal.confirm({
      title: '放弃未保存修改？',
      content: '当前文件内容尚未保存，继续操作会丢弃这些修改。',
      okText: '放弃修改',
      cancelText: '取消',
      okButtonProps: { danger: true },
      onOk: () => {
        resolve(true)
      },
      onCancel: () => {
        resolve(false)
      }
    })
  })

const updateFileSummary = (
  files: XcodeAgentMarkdownFileSummary[],
  nextFile: XcodeAgentMarkdownFileSummary
): XcodeAgentMarkdownFileSummary[] =>
  files.map((file) => (file.name === nextFile.name ? nextFile : file))

function AgentsPage(): React.JSX.Element {
  const api = useApi()
  const [files, setFiles] = useState<XcodeAgentMarkdownFileSummary[]>([])
  const [selectedFileName, setSelectedFileName] = useState<XcodeAgentMarkdownFileName | null>(null)
  const [selectedFile, setSelectedFile] = useState<XcodeAgentMarkdownFileContent | null>(null)
  const [savedContent, setSavedContent] = useState('')
  const [draftContent, setDraftContent] = useState('')
  const [previewEnabled, setPreviewEnabled] = useState(false)
  const [loadingList, setLoadingList] = useState(true)
  const [loadingFile, setLoadingFile] = useState(false)
  const [saving, setSaving] = useState(false)
  const [errorMessage, setErrorMessage] = useState('')

  const hasUnsavedChanges = draftContent !== savedContent
  const selectedSummary = useMemo(
    () => files.find((file) => file.name === selectedFileName) ?? selectedFile,
    [files, selectedFile, selectedFileName]
  )

  const loadMarkdownFile = useCallback(
    async (fileName: XcodeAgentMarkdownFileName): Promise<void> => {
      setLoadingFile(true)
      setErrorMessage('')

      try {
        const nextFile = await api.xcodeAgent.getMarkdownFile(fileName)

        setSelectedFileName(nextFile.name)
        setSelectedFile(nextFile)
        setSavedContent(nextFile.content)
        setDraftContent(nextFile.content)
        setFiles((currentFiles) => updateFileSummary(currentFiles, nextFile))
      } catch (error) {
        console.error(error)
        setErrorMessage('读取文件失败')
      } finally {
        setLoadingFile(false)
      }
    },
    [api]
  )

  const loadMarkdownFiles = useCallback(
    async (preferredFileName?: XcodeAgentMarkdownFileName): Promise<void> => {
      setLoadingList(true)
      setErrorMessage('')

      try {
        const nextFiles = await api.xcodeAgent.listMarkdownFiles()
        const nextSelectedFileName =
          preferredFileName && nextFiles.some((file) => file.name === preferredFileName)
            ? preferredFileName
            : nextFiles[0]?.name

        setFiles(nextFiles)

        if (nextSelectedFileName) {
          await loadMarkdownFile(nextSelectedFileName)
        } else {
          setSelectedFileName(null)
          setSelectedFile(null)
          setSavedContent('')
          setDraftContent('')
        }
      } catch (error) {
        console.error(error)
        setFiles([])
        setSelectedFile(null)
        setSelectedFileName(null)
        setSavedContent('')
        setDraftContent('')
        setErrorMessage('读取文件列表失败')
      } finally {
        setLoadingList(false)
      }
    },
    [api, loadMarkdownFile]
  )

  useEffect(() => {
    void loadMarkdownFiles()
  }, [loadMarkdownFiles])

  const continueAfterDiscard = async (): Promise<boolean> => {
    if (!hasUnsavedChanges) {
      return true
    }

    return confirmDiscardChanges()
  }

  const handleSelectFile = async (fileName: XcodeAgentMarkdownFileName): Promise<void> => {
    if (fileName === selectedFileName) {
      return
    }

    if (!(await continueAfterDiscard())) {
      return
    }

    await loadMarkdownFile(fileName)
  }

  const handleRefresh = async (): Promise<void> => {
    if (!(await continueAfterDiscard())) {
      return
    }

    await loadMarkdownFiles(selectedFileName ?? undefined)
  }

  const handleReset = (): void => {
    if (!hasUnsavedChanges) {
      return
    }

    setDraftContent(savedContent)
  }

  const handleSave = async (): Promise<void> => {
    if (!selectedFileName || !hasUnsavedChanges) {
      return
    }

    setSaving(true)
    setErrorMessage('')

    try {
      const nextFile = await api.xcodeAgent.saveMarkdownFile(selectedFileName, draftContent)

      setSelectedFile(nextFile)
      setSavedContent(nextFile.content)
      setDraftContent(nextFile.content)
      setFiles((currentFiles) => updateFileSummary(currentFiles, nextFile))
      message.success('保存成功')
    } catch (error) {
      console.error(error)
      message.error('保存失败')
      setErrorMessage('保存文件失败')
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className="agents-page">
      <aside className="agents-page__sidebar">
        <div className="agents-page__sidebar-header">
          <div>
            <h1 className="agents-page__sidebar-title">核心文件</h1>
            <p className="agents-page__sidebar-description">引导角色、身份和工具指南。</p>
          </div>
          <Button
            aria-label="刷新文件列表"
            icon={<ReloadOutlined />}
            loading={loadingList}
            size="small"
            onClick={() => void handleRefresh()}
          />
        </div>

        <div className="agents-page__file-list">
          {loadingList && files.length === 0 ? (
            <div className="agents-page__file-loading">
              <Spin />
            </div>
          ) : null}
          {!loadingList && files.length === 0 ? <Empty description="暂无文件" /> : null}
          {files.map((file) => (
            <button
              className={[
                'agents-page__file-card',
                file.name === selectedFileName ? 'agents-page__file-card--active' : ''
              ]
                .filter(Boolean)
                .join(' ')}
              key={file.name}
              type="button"
              onClick={() => void handleSelectFile(file.name)}
            >
              <span className="agents-page__file-dot" />
              <span className="agents-page__file-main">
                <span className="agents-page__file-name">{file.name}</span>
                <span className="agents-page__file-meta">
                  {formatFileSize(file.size)} · {formatRelativeTime(file.updatedAt)}
                </span>
              </span>
              <Switch
                checked={file.enabled}
                className="agents-page__file-switch"
                size="small"
                onClick={(_, event) => {
                  event.stopPropagation()
                }}
              />
            </button>
          ))}
        </div>
      </aside>

      <main className="agents-page__editor">
        <header className="agents-page__editor-header">
          <div className="agents-page__file-heading">
            <h2 className="agents-page__editor-title">{selectedFileName ?? '未选择文件'}</h2>
            <Typography.Text className="agents-page__editor-path" copyable={!!selectedFile?.path}>
              {selectedFile?.path ?? '-'}
            </Typography.Text>
          </div>
          <div className="agents-page__editor-actions">
            <Button
              disabled={!hasUnsavedChanges || loadingFile || saving}
              icon={<UndoOutlined />}
              onClick={handleReset}
            >
              重置
            </Button>
            <Button
              disabled={!hasUnsavedChanges || loadingFile}
              icon={<SaveOutlined />}
              loading={saving}
              type="primary"
              onClick={() => void handleSave()}
            >
              保存
            </Button>
          </div>
        </header>

        {errorMessage ? (
          <Alert className="agents-page__alert" message={errorMessage} showIcon type="error" />
        ) : null}

        <div className="agents-page__content-header">
          <span className="agents-page__content-title">内容</span>
          <label className="agents-page__preview-toggle">
            <span>预览</span>
            <Switch checked={previewEnabled} size="small" onChange={setPreviewEnabled} />
          </label>
        </div>

        <div className="agents-page__content">
          {loadingFile ? (
            <div className="agents-page__content-loading">
              <Spin tip="读取文件..." />
            </div>
          ) : previewEnabled ? (
            <pre className="agents-page__preview">{draftContent}</pre>
          ) : (
            <Input.TextArea
              className="agents-page__textarea"
              disabled={!selectedFile}
              value={draftContent}
              onChange={(event) => setDraftContent(event.target.value)}
            />
          )}
        </div>

        {selectedSummary ? (
          <div className="agents-page__status">
            {formatFileSize(selectedSummary.size)} · {formatRelativeTime(selectedSummary.updatedAt)}
          </div>
        ) : null}
      </main>
    </section>
  )
}

export default AgentsPage

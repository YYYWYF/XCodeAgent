import { InboxOutlined } from '@ant-design/icons'
import { Alert, Descriptions, Modal, Spin, Typography, Upload, message } from 'antd'
import type { UploadProps } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useState } from 'react'
import { importUserSkillArchive } from '../../service/userSkills'
import { cx } from '../../utils'
import { inspectSkillZip, type SkillZipPreview } from './skillZip'
import './SkillZipImportModal.less'

const { Dragger } = Upload
const { Text } = Typography

type Props = {
  existingSkillNames: string[]
  onClose: () => void
  onImported: () => Promise<void> | void
  open: boolean
  theme: 'light' | 'dark'
}

export default function SkillZipImportModal({
  existingSkillNames,
  onClose,
  onImported,
  open,
  theme
}: Props): ReactElement {
  const [error, setError] = useState('')
  const [importing, setImporting] = useState(false)
  const [preview, setPreview] = useState<SkillZipPreview>()
  const [validating, setValidating] = useState(false)

  useEffect(() => {
    if (open) return
    setError('')
    setImporting(false)
    setPreview(undefined)
    setValidating(false)
  }, [open])

  const beforeUpload: UploadProps['beforeUpload'] = async (file) => {
    setPreview(undefined)
    setError('')
    setValidating(true)
    try {
      const result = await inspectSkillZip(await file.arrayBuffer(), file.name)
      if (existingSkillNames.includes(result.name)) {
        throw new Error(`技能 ${result.name} 已存在，请更换 name 后重新打包。`)
      }
      setPreview(result)
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : 'ZIP 校验失败。')
    } finally {
      setValidating(false)
    }
    return Upload.LIST_IGNORE
  }

  const handleImport = async (): Promise<void> => {
    if (!preview || importing) return
    setImporting(true)
    setError('')
    try {
      await importUserSkillArchive({
        archiveBase64: preview.archiveBase64,
        fileName: preview.fileName
      })
      message.success(`技能 ${preview.name} 已导入`)
      await onImported()
      onClose()
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : '技能导入失败。')
    } finally {
      setImporting(false)
    }
  }

  return (
    <Modal
      cancelButtonProps={{ disabled: importing }}
      cancelText="取消"
      centered
      closable={!importing}
      destroyOnClose
      maskClosable={!importing}
      okButtonProps={{ disabled: !preview || validating }}
      okText="导入技能"
      onCancel={onClose}
      onOk={() => void handleImport()}
      open={open}
      confirmLoading={importing}
      title="ZIP 上传"
      width={640}
      wrapClassName={cx('skill-zip-import-modal', `theme-${theme}`)}
    >
      <Dragger
        accept=".zip,application/zip,application/x-zip-compressed"
        beforeUpload={beforeUpload}
        disabled={importing || validating}
        maxCount={1}
        multiple={false}
        showUploadList={false}
      >
        <p className="ant-upload-drag-icon">{validating ? <Spin /> : <InboxOutlined />}</p>
        <p className="ant-upload-text">点击或拖拽一个技能 ZIP 包到此处</p>
        <p className="ant-upload-hint">SKILL.md 可位于根目录或唯一顶层目录；校验通过后才能导入。</p>
      </Dragger>

      {error && (
        <Alert
          className={cx('skill-zip-result')}
          message="无法导入"
          description={error}
          showIcon
          type="error"
        />
      )}
      {preview && (
        <div className={cx('skill-zip-result')}>
          <Alert message="ZIP 校验通过" showIcon type="success" />
          <Descriptions column={1} size="small">
            <Descriptions.Item label="名称">{preview.name}</Descriptions.Item>
            <Descriptions.Item label="描述">{preview.description}</Descriptions.Item>
            <Descriptions.Item label="文件">
              {preview.fileName}（{preview.fileCount} 个文件）
            </Descriptions.Item>
            <Descriptions.Item label="目标目录">
              <Text code>{preview.name}</Text>
            </Descriptions.Item>
          </Descriptions>
        </div>
      )}
    </Modal>
  )
}

import { CodeOutlined, ExclamationCircleOutlined, FileTextOutlined } from '@ant-design/icons'
import { Alert, Button, Drawer, Form, Input, Modal, Spin, Typography, message } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useMemo, useState } from 'react'
import { requestUserSkillDocument, saveUserSkillDocument } from '../../service/userSkills'
import type { UserSkill, UserSkillDocument } from '../../typings'
import { cx } from '../../utils'
import { validateSkillContent } from './skillContent'
import './SkillEditorDrawer.less'

const { Text } = Typography
const { TextArea } = Input

type Props = {
  onClose: () => void
  onSaved: () => Promise<void> | void
  skill?: UserSkill
  theme: 'light' | 'dark'
}

export default function SkillEditorDrawer({ onClose, onSaved, skill, theme }: Props): ReactElement {
  const [content, setContent] = useState('')
  const [document, setDocument] = useState<UserSkillDocument>()
  const [loadError, setLoadError] = useState('')
  const [loading, setLoading] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (!skill) {
      setContent('')
      setDocument(undefined)
      setLoadError('')
      setSaveError('')
      return
    }

    let active = true
    setLoading(true)
    setLoadError('')
    setSaveError('')
    setDocument(undefined)
    setContent('')
    void requestUserSkillDocument(skill.relativePath)
      .then((result) => {
        if (!active) return
        setDocument(result)
        setContent(result.content)
      })
      .catch((caughtError) => {
        if (!active) return
        setLoadError(caughtError instanceof Error ? caughtError.message : '技能内容读取失败。')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [skill])

  const validation = useMemo(() => validateSkillContent(content), [content])
  const dirty = Boolean(document && content !== document.content)

  const handleClose = (): void => {
    if (!dirty) {
      onClose()
      return
    }
    Modal.confirm({
      cancelText: '继续编辑',
      centered: true,
      className: cx('skill-discard-confirm', `theme-${theme}`),
      content: '当前技能内容尚未保存，关闭后修改将丢失。',
      icon: <ExclamationCircleOutlined />,
      okButtonProps: { danger: true },
      okText: '放弃修改',
      onOk: onClose,
      title: '放弃未保存的修改？'
    })
  }

  const handleSave = async (): Promise<void> => {
    if (!document || !dirty || !validation.valid || saving) return
    setSaving(true)
    setSaveError('')
    try {
      await saveUserSkillDocument({
        relativePath: document.relativePath,
        content,
        expectedRevision: document.revision
      })
      message.success('技能已保存')
      await onSaved()
    } catch (caughtError) {
      setSaveError(caughtError instanceof Error ? caughtError.message : '技能保存失败。')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Drawer
      className={cx('skill-editor-drawer')}
      contentWrapperStyle={{ maxWidth: 'calc(100% - 16px)' }}
      destroyOnClose
      footer={
        <div className={cx('skill-editor-footer')}>
          <Button disabled={saving} onClick={handleClose}>
            取消
          </Button>
          <Button
            disabled={!document || !dirty || !validation.valid || loading}
            loading={saving}
            onClick={() => void handleSave()}
            type="primary"
          >
            保存
          </Button>
        </div>
      }
      getContainer={false}
      keyboard={false}
      maskClosable={false}
      onClose={handleClose}
      open={Boolean(skill)}
      style={{ position: 'absolute' }}
      title={
        <div className={cx('skill-editor-title')}>
          <span className={cx('skill-editor-title-icon')} aria-hidden="true">
            <CodeOutlined />
          </span>
          <span>
            <span className={cx('skill-editor-title-text')}>编辑技能</span>
            <span className={cx('skill-editor-title-caption')}>SKILL.md</span>
          </span>
        </div>
      }
      width={720}
    >
      <div className={cx('skill-editor-body')}>
        {loading ? (
          <div className={cx('skill-editor-loading')}>
            <Spin />
            <Text type="secondary">正在读取完整 SKILL.md...</Text>
          </div>
        ) : loadError ? (
          <Alert description={loadError} message="无法读取技能内容" showIcon type="error" />
        ) : document ? (
          <>
            {saveError && (
              <Alert
                className={cx('skill-editor-save-error')}
                description={saveError}
                message="保存失败"
                showIcon
                type="error"
              />
            )}
            <Form className={cx('skill-editor-form')} layout="vertical">
              <Form.Item className={cx('skill-name-item')} label="技能名称" required>
                <Input
                  aria-label="技能名称"
                  placeholder="技能内容中缺少有效的 name"
                  prefix={<CodeOutlined aria-hidden="true" />}
                  readOnly
                  value={validation.name}
                />
              </Form.Item>
              <Form.Item
                className={cx('skill-content-item')}
                help={validation.valid ? undefined : validation.error}
                label="技能内容"
                required
                validateStatus={validation.valid ? undefined : 'error'}
              >
                <div className={cx('skill-content-editor')}>
                  <div className={cx('skill-content-editor-bar')}>
                    <span>
                      <FileTextOutlined aria-hidden="true" />
                      SKILL.md
                    </span>
                    <span>YAML + Markdown</span>
                  </div>
                  <TextArea
                    aria-invalid={!validation.valid}
                    aria-label="技能内容"
                    className={cx('skill-content-textarea')}
                    onChange={(event) => {
                      setContent(event.target.value)
                      setSaveError('')
                    }}
                    spellCheck={false}
                    value={content}
                  />
                </div>
              </Form.Item>
            </Form>
          </>
        ) : null}
      </div>
    </Drawer>
  )
}

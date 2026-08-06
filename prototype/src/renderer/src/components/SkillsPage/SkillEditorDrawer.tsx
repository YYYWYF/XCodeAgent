import { CodeOutlined, ExclamationCircleOutlined, FileTextOutlined } from '@ant-design/icons'
import { Alert, Button, Drawer, Form, Input, Modal, Spin, Typography, message } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useMemo, useState } from 'react'
import {
  createUserSkillDocument,
  requestUserSkillDocument,
  saveUserSkillDocument
} from '../../service/userSkills'
import { isAuthenticationFailure } from '../../service/authentication'
import type { UserSkill, UserSkillDocument } from '../../typings'
import { cx } from '../../utils'
import {
  CREATE_SKILL_CONTENT_PLACEHOLDER,
  readSkillNameFromContent,
  syncSkillNameToContent,
  validateSkillContent
} from './skillContent'
import './SkillEditorDrawer.less'

const { Text } = Typography
const { TextArea } = Input

function getWorkbenchDrawerContainer(): HTMLElement {
  return document.querySelector<HTMLElement>(`.${cx('workbench-shell')}`) ?? document.body
}

type SharedProps = {
  onClose: () => void
  onSaved: () => Promise<void> | void
  theme: 'light' | 'dark'
}

type Props = SharedProps &
  (
    | { mode: 'create'; open: boolean; skill?: never }
    | { mode?: 'edit'; open?: never; skill?: UserSkill }
  )

export default function SkillEditorDrawer(props: Props): ReactElement {
  const { onClose, onSaved, theme } = props
  const isCreate = props.mode === 'create'
  const skill = isCreate ? undefined : props.skill
  const open = isCreate ? props.open : Boolean(skill)
  const [content, setContent] = useState('')
  const [document, setDocument] = useState<UserSkillDocument>()
  const [loadError, setLoadError] = useState('')
  const [loading, setLoading] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [saving, setSaving] = useState(false)
  const [skillName, setSkillName] = useState('')
  const [createTouched, setCreateTouched] = useState(false)

  useEffect(() => {
    if (!open) {
      setContent('')
      setDocument(undefined)
      setLoadError('')
      setSaveError('')
      setLoading(false)
      setSkillName('')
      setCreateTouched(false)
      return
    }
    if (isCreate) {
      setContent('')
      setDocument(undefined)
      setLoadError('')
      setSaveError('')
      setLoading(false)
      setSkillName('')
      setCreateTouched(false)
      return
    }
    if (!skill) return

    let active = true
    setLoading(true)
    setLoadError('')
    setSaveError('')
    setDocument(undefined)
    setContent('')
    setSkillName('')
    void requestUserSkillDocument(skill.relativePath)
      .then((result) => {
        if (!active) return
        setDocument(result)
        setContent(result.content)
        setSkillName(readSkillNameFromContent(result.content) || result.name)
      })
      .catch((caughtError) => {
        if (!active) return
        setLoadError(
          isAuthenticationFailure(caughtError)
            ? '请重新登录后重新打开技能。'
            : caughtError instanceof Error
              ? caughtError.message
              : '技能内容读取失败。'
        )
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [isCreate, open, skill])

  const validation = useMemo(
    () => validateSkillContent(content, isCreate ? 'create' : 'edit'),
    [content, isCreate]
  )
  const dirty = isCreate
    ? content.length > 0 || skillName.length > 0
    : Boolean(document && content !== document.content)
  const ready = isCreate || Boolean(document)
  const showContentError = !validation.valid && (!isCreate || createTouched)

  const handleSkillNameChange = (name: string): void => {
    setSkillName(name)
    setContent((currentContent) => syncSkillNameToContent(currentContent, name))
    if (isCreate) setCreateTouched(true)
    setSaveError('')
  }

  const handleContentChange = (nextContent: string): void => {
    setContent(nextContent)
    setSkillName(readSkillNameFromContent(nextContent))
    if (isCreate) setCreateTouched(true)
    setSaveError('')
  }

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
    if (!ready || !dirty || !validation.valid || saving) return
    setSaving(true)
    setSaveError('')
    try {
      if (isCreate) {
        await createUserSkillDocument({ content })
      } else {
        if (!document) return
        await saveUserSkillDocument({
          relativePath: document.relativePath,
          content,
          expectedRevision: document.revision
        })
      }
      message.success(isCreate ? '技能已创建' : '技能已保存')
      await onSaved()
    } catch (caughtError) {
      if (isAuthenticationFailure(caughtError)) return
      setSaveError(
        caughtError instanceof Error
          ? caughtError.message
          : isCreate
            ? '技能创建失败。'
            : '技能保存失败。'
      )
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
            disabled={!ready || !dirty || !validation.valid || loading}
            loading={saving}
            onClick={() => void handleSave()}
            type="primary"
          >
            {isCreate ? '创建' : '保存'}
          </Button>
        </div>
      }
      getContainer={getWorkbenchDrawerContainer}
      keyboard={false}
      maskClosable={false}
      onClose={handleClose}
      open={open}
      title={
        <div className={cx('skill-editor-title')}>
          <span className={cx('skill-editor-title-icon')} aria-hidden="true">
            <CodeOutlined />
          </span>
          <span>
            <span className={cx('skill-editor-title-text')}>
              {isCreate ? '创建技能' : '编辑技能'}
            </span>
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
        ) : ready ? (
          <>
            {saveError && (
              <Alert
                className={cx('skill-editor-save-error')}
                description={saveError}
                message={isCreate ? '创建失败' : '保存失败'}
                showIcon
                type="error"
              />
            )}
            <Form className={cx('skill-editor-form')} layout="vertical">
              <Form.Item className={cx('skill-name-item')} label="技能名称" required>
                <Input
                  aria-label="技能名称"
                  autoComplete="off"
                  onChange={(event) => handleSkillNameChange(event.target.value)}
                  placeholder="请输入技能名称"
                  prefix={<CodeOutlined aria-hidden="true" />}
                  value={skillName}
                />
              </Form.Item>
              <Form.Item
                className={cx('skill-content-item')}
                help={showContentError ? validation.error : undefined}
                label="技能内容"
                required
                validateStatus={showContentError ? 'error' : undefined}
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
                    aria-invalid={showContentError}
                    aria-label="技能内容"
                    className={cx('skill-content-textarea')}
                    onChange={(event) => handleContentChange(event.target.value)}
                    placeholder={isCreate ? CREATE_SKILL_CONTENT_PLACEHOLDER : undefined}
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

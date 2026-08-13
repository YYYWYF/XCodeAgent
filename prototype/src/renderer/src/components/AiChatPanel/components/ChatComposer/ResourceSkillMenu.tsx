import {
  CheckOutlined,
  ApiOutlined,
  FileTextOutlined,
  LockOutlined,
  PaperClipOutlined,
  PlusOutlined,
  RightOutlined,
  SearchOutlined,
  ToolOutlined
} from '@ant-design/icons'
import { Button, Empty, Input, message, Popover, Spin, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { requestUserSkills } from '../../../../service/userSkills'
import type { ChatMessageSkill, UserSkill } from '../../../../typings'
import { cx } from '../../../../utils'
import {
  enabledUserSkills,
  reconcileEnabledChatSkills
} from '../../../SkillsPage/skillCatalog'

const { Text } = Typography

type ResourceSkillMenuProps = {
  artifactResources?: ComposerArtifactResource[]
  disabled: boolean
  selectedSkills: ChatMessageSkill[]
  onSelectedSkillsChange: (skills: ChatMessageSkill[]) => void
}

export type ComposerArtifactResource = {
  accessMessage: string
  accessMode: 'unavailable' | 'read' | 'write'
  id: string
  name: string
  path: string
  type: 'document' | 'page' | 'endpoint' | 'model'
}

/** 把菜单挂到输入框内，使明暗主题变量和定位上下文保持一致。 */
function getResourcePopupContainer(triggerNode: HTMLElement): HTMLElement {
  return triggerNode.parentElement || triggerNode
}

/** 提供资源一级菜单与可搜索、多选的技能二级菜单。 */
export default function ResourceSkillMenu({
  artifactResources = [],
  disabled,
  selectedSkills,
  onSelectedSkillsChange
}: ResourceSkillMenuProps): ReactElement {
  const [visible, setVisible] = useState(false)
  const [skillPanelOpen, setSkillPanelOpen] = useState(false)
  const [artifactPanelOpen, setArtifactPanelOpen] = useState(false)
  const [selectedArtifactIds, setSelectedArtifactIds] = useState<string[]>([])
  const [search, setSearch] = useState('')
  const [skills, setSkills] = useState<UserSkill[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const selectedSkillsRef = useRef(selectedSkills)
  const onSelectedSkillsChangeRef = useRef(onSelectedSkillsChange)
  const selectedNames = useMemo(
    () => new Set(selectedSkills.map((skill) => skill.name)),
    [selectedSkills]
  )
  const filteredSkills = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    if (!query) return skills
    return skills.filter((skill) =>
      `${skill.name}\n${skill.description}`.toLocaleLowerCase().includes(query)
    )
  }, [search, skills])

  useEffect(() => {
    selectedSkillsRef.current = selectedSkills
    onSelectedSkillsChangeRef.current = onSelectedSkillsChange
  }, [onSelectedSkillsChange, selectedSkills])

  useEffect(() => {
    if (!visible) return
    let active = true
    setLoading(true)
    setError('')
    requestUserSkills()
      .then((catalog) => {
        if (!active) return
        const availableSkills = enabledUserSkills(catalog.skills)
        setSkills(availableSkills)
        const currentSelection = selectedSkillsRef.current
        const reconciled = reconcileEnabledChatSkills(currentSelection, catalog.skills)
        if (reconciled.length !== currentSelection.length) {
          onSelectedSkillsChangeRef.current(reconciled)
          message.warning('部分已选技能已失效，标签已移除。')
        } else if (
          reconciled.some(
            (skill, index) => skill.description !== currentSelection[index]?.description
          )
        ) {
          onSelectedSkillsChangeRef.current(reconciled)
        }
      })
      .catch((caughtError) => {
        if (!active) return
        setError(caughtError instanceof Error ? caughtError.message : '读取技能列表失败。')
      })
      .finally(() => {
        if (active) setLoading(false)
      })
    return () => {
      active = false
    }
  }, [visible])

  /** 同步弹层开关，并在关闭时复位二级菜单和搜索词。 */
  const handleVisibleChange = (nextVisible: boolean): void => {
    setVisible(nextVisible)
    if (!nextVisible) {
      setSkillPanelOpen(false)
      setArtifactPanelOpen(false)
      setSearch('')
    }
  }

  /** 切换一个技能后立即关闭面板，用户可再次打开以继续多选。 */
  const handleToggleSkill = (skill: UserSkill): void => {
    const nextSkills = selectedNames.has(skill.name)
      ? selectedSkills.filter((item) => item.name !== skill.name)
      : [...selectedSkills, { name: skill.name, description: skill.description }]
    onSelectedSkillsChange(nextSkills)
    handleVisibleChange(false)
  }

  /** 把可用产物加入当前上下文；只读产物可引用但不会获得编辑权。 */
  const handleToggleArtifact = (artifact: ComposerArtifactResource): void => {
    if (artifact.accessMode === 'unavailable') return
    setSelectedArtifactIds((current) =>
      current.includes(artifact.id)
        ? current.filter((artifactId) => artifactId !== artifact.id)
        : [...current, artifact.id]
    )
  }

  const content = (
    <div className={cx('composer-resource-popover')}>
      <div className={cx('composer-resource-primary')}>
        <button
          className={cx('composer-resource-item', skillPanelOpen && 'active')}
          onClick={() => {
            setArtifactPanelOpen(false)
            setSkillPanelOpen(true)
          }}
          type="button"
        >
          <ToolOutlined />
          <span>技能</span>
          <RightOutlined />
        </button>
        <button
          className={cx('composer-resource-item', artifactPanelOpen && 'active')}
          onClick={() => {
            setSkillPanelOpen(false)
            setArtifactPanelOpen(true)
          }}
          type="button"
        >
          <PaperClipOutlined />
          <span>添加文件</span>
          <RightOutlined />
        </button>
      </div>
      {skillPanelOpen && (
        <div className={cx('composer-skill-panel')}>
          <Input
            allowClear
            aria-label="搜索技能"
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜索技能"
            prefix={<SearchOutlined />}
            value={search}
          />
          <div className={cx('composer-skill-list')}>
            {loading ? (
              <div className={cx('composer-skill-state')}>
                <Spin size="small" />
              </div>
            ) : error ? (
              <div className={cx('composer-skill-state', 'error')}>
                <Text type="danger">{error}</Text>
              </div>
            ) : filteredSkills.length === 0 ? (
              <Empty
                description={search ? '没有匹配技能' : '暂无可用技能'}
                image={Empty.PRESENTED_IMAGE_SIMPLE}
              />
            ) : (
              filteredSkills.map((skill) => {
                const selected = selectedNames.has(skill.name)
                return (
                  <button
                    aria-pressed={selected}
                    className={cx('composer-skill-item', selected && 'selected')}
                    key={`${skill.directoryName}:${skill.name}`}
                    onClick={() => handleToggleSkill(skill)}
                    type="button"
                  >
                    <span className={cx('composer-skill-avatar')}>
                      {skill.name.slice(0, 1).toUpperCase()}
                    </span>
                    <span className={cx('composer-skill-copy')}>
                      <Text>{skill.name}</Text>
                      <Text type="secondary">{skill.description || '暂无描述'}</Text>
                    </span>
                    <span className={cx('composer-skill-check')}>
                      {selected && <CheckOutlined />}
                    </span>
                  </button>
                )
              })
            )}
          </div>
        </div>
      )}
      {artifactPanelOpen && (
        <div className={cx('composer-artifact-panel')}>
          <header>
            <Text strong>应用产物</Text>
            <Text type="secondary">编辑权随默认对话锁定</Text>
          </header>
          <div className={cx('composer-artifact-list')}>
            {artifactResources.length > 0 ? (
              artifactResources.map((artifact) => {
                const selected = selectedArtifactIds.includes(artifact.id)
                const locked = artifact.accessMode !== 'write'
                return (
                  <button
                    aria-pressed={selected}
                    className={cx(
                      'composer-artifact-item',
                      selected && 'selected',
                      artifact.accessMode
                    )}
                    disabled={artifact.accessMode === 'unavailable'}
                    key={artifact.id}
                    onClick={() => handleToggleArtifact(artifact)}
                    title={artifact.accessMessage}
                    type="button"
                  >
                    <span className={cx('composer-artifact-icon')}>
                      {artifact.type === 'endpoint' ? <ApiOutlined /> : <FileTextOutlined />}
                    </span>
                    <span className={cx('composer-artifact-copy')}>
                      <Text>{artifact.name}</Text>
                      <Text code type="secondary">{artifact.path}</Text>
                    </span>
                    <span className={cx('composer-artifact-access', artifact.accessMode)}>
                      {locked ? <LockOutlined /> : <CheckOutlined />}
                      {artifact.accessMode === 'write'
                        ? '可编辑'
                        : artifact.accessMode === 'read'
                          ? '只读'
                          : '未生成'}
                    </span>
                  </button>
                )
              })
            ) : (
              <Empty description="暂无可引用产物" image={Empty.PRESENTED_IMAGE_SIMPLE} />
            )}
          </div>
        </div>
      )}
    </div>
  )

  return (
    <Popover
      content={content}
      getPopupContainer={getResourcePopupContainer}
      overlayClassName={cx('composer-resource-overlay')}
      placement="topLeft"
      trigger="click"
      visible={visible}
      onVisibleChange={handleVisibleChange}
    >
      <Button
        aria-label="添加资源"
        className={cx('composer-resource-button')}
        disabled={disabled}
        icon={<PlusOutlined />}
        shape="circle"
        title="添加技能或文件"
        type="text"
      />
    </Popover>
  )
}

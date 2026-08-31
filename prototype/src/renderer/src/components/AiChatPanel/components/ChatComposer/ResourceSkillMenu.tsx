import {
  CheckOutlined,
  FileTextOutlined,
  PaperClipOutlined,
  PlusOutlined,
  RightOutlined,
  SearchOutlined,
  ToolOutlined
} from '@ant-design/icons'
import { Button, Empty, Input, message, Popover, Spin, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { WorkspaceSourceFile } from '../../../../mock/workspaceFiles'
import { requestUserSkills } from '../../../../service/userSkills'
import type { ChatMessageSkill, UserSkill } from '../../../../typings'
import { cx } from '../../../../utils'
import { enabledUserSkills, reconcileEnabledChatSkills } from '../../../SkillsPage/skillCatalog'

const { Text } = Typography

type ResourceSkillMenuProps = {
  availableFiles: WorkspaceSourceFile[]
  disabled: boolean
  onSelectedFilePathsChange: (paths: string[]) => void
  selectedSkills: ChatMessageSkill[]
  selectedFilePaths: string[]
  onSelectedSkillsChange: (skills: ChatMessageSkill[]) => void
}

/** 把菜单挂到输入框内，使浅色样式变量和定位上下文保持一致。 */
function getResourcePopupContainer(triggerNode: HTMLElement): HTMLElement {
  return triggerNode.parentElement || triggerNode
}

/** 提供资源一级菜单与可搜索、多选的技能二级菜单。 */
export default function ResourceSkillMenu({
  availableFiles,
  disabled,
  onSelectedFilePathsChange,
  selectedSkills,
  selectedFilePaths,
  onSelectedSkillsChange
}: ResourceSkillMenuProps): ReactElement {
  const [visible, setVisible] = useState(false)
  const [skillPanelOpen, setSkillPanelOpen] = useState(false)
  const [filePanelOpen, setFilePanelOpen] = useState(false)
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
  const filteredFiles = useMemo(() => {
    const query = search.trim().toLocaleLowerCase()
    if (!query) return availableFiles
    return availableFiles.filter((file) => file.path.toLocaleLowerCase().includes(query))
  }, [availableFiles, search])

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
      setFilePanelOpen(false)
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

  /** 切换本次 Workflow 要读取的文件，只更新当前输入上下文，不写入会话归属。 */
  const handleToggleFile = (file: WorkspaceSourceFile): void => {
    const selected = selectedFilePaths.includes(file.path)
    onSelectedFilePathsChange(
      selected
        ? selectedFilePaths.filter((path) => path !== file.path)
        : [...selectedFilePaths, file.path]
    )
  }

  const skillPanel = (
    <div className={cx('composer-skill-panel')}>
      <div className={cx('composer-skill-heading')}>
        <ToolOutlined />
        <Text strong>添加技能</Text>
      </div>
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
                <span className={cx('composer-skill-check')}>{selected && <CheckOutlined />}</span>
              </button>
            )
          })
        )}
      </div>
    </div>
  )

  const filePanel = (
    <div className={cx('composer-file-panel')}>
      <header>
        <Text strong>添加文件</Text>
        <Text type="secondary">本次发送时带入 Workflow</Text>
      </header>
      <Input
        allowClear
        aria-label="搜索文件"
        onChange={(event) => setSearch(event.target.value)}
        placeholder="搜索文件"
        prefix={<SearchOutlined />}
        value={search}
      />
      <div className={cx('composer-file-list')}>
        {filteredFiles.length > 0 ? (
          filteredFiles.map((file) => {
            const selected = selectedFilePaths.includes(file.path)
            const fileName = file.path.split('/').pop() || file.path
            return (
              <button
                aria-pressed={selected}
                className={cx('composer-file-item', selected && 'selected')}
                key={file.path}
                onClick={() => handleToggleFile(file)}
                title={file.path}
                type="button"
              >
                <span className={cx('composer-file-icon')}>
                  <FileTextOutlined />
                </span>
                <span className={cx('composer-file-copy')}>
                  <Text>{fileName}</Text>
                  <Text code type="secondary">
                    {file.path}
                  </Text>
                </span>
                <span className={cx('composer-file-check')}>{selected && <CheckOutlined />}</span>
              </button>
            )
          })
        ) : (
          <Empty
            description={search ? '没有匹配文件' : '暂无可选文件'}
            image={Empty.PRESENTED_IMAGE_SIMPLE}
          />
        )}
      </div>
    </div>
  )

  const content = (
    <div className={cx('composer-resource-popover')}>
      <div className={cx('composer-resource-primary')}>
        <button
          className={cx('composer-resource-item', skillPanelOpen && 'active')}
          onClick={() => {
            setFilePanelOpen(false)
            setSkillPanelOpen(true)
            setSearch('')
          }}
          type="button"
        >
          <ToolOutlined />
          <span>技能</span>
          <RightOutlined />
        </button>
        <button
          className={cx('composer-resource-item', filePanelOpen && 'active')}
          onClick={() => {
            setSkillPanelOpen(false)
            setFilePanelOpen(true)
            setSearch('')
          }}
          type="button"
        >
          <PaperClipOutlined />
          <span>添加文件</span>
          {selectedFilePaths.length > 0 ? <small>{selectedFilePaths.length}</small> : null}
          <RightOutlined />
        </button>
      </div>
      {skillPanelOpen ? skillPanel : null}
      {filePanelOpen ? filePanel : null}
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
        aria-label="添加技能或文件"
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

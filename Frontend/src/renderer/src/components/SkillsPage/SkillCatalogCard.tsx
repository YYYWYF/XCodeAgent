import { AppstoreOutlined, DeleteOutlined } from '@ant-design/icons'
import { Button, Switch, Tag, Typography } from 'antd'
import type { KeyboardEvent, ReactElement } from 'react'
import type { BuiltinSkill, UserSkill } from '../../typings'
import { cx } from '../../utils'
import type { SkillCategory } from './skillCatalog'

const { Paragraph, Title } = Typography

type Props = {
  active: boolean
  category: SkillCategory
  deleting: boolean
  onDelete: (skill: UserSkill) => void
  onOpen: (skill: UserSkill) => void
  onToggle: (skill: UserSkill, enabled: boolean) => void
  skill: UserSkill | BuiltinSkill
  toggling: boolean
}

/** 把 ISO 更新时间格式化为技能卡片使用的本地时间。 */
function formatUpdatedAt(value: string): string {
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp)) return '未知时间'
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(timestamp)
}

/** 处理用户技能卡片的键盘打开操作。 */
function handleUserCardKeyDown(
  event: KeyboardEvent<HTMLElement>,
  skill: UserSkill,
  onOpen: (skill: UserSkill) => void
): void {
  if (event.key !== 'Enter' && event.key !== ' ') return
  event.preventDefault()
  onOpen(skill)
}

/** 渲染只读内置技能卡片或带启停、编辑和删除能力的用户技能卡片。 */
export default function SkillCatalogCard({
  active,
  category,
  deleting,
  onDelete,
  onOpen,
  onToggle,
  skill,
  toggling
}: Props): ReactElement {
  const userSkill = category === 'user' ? (skill as UserSkill) : undefined
  const interactionLocked = Boolean(userSkill && (toggling || deleting))
  return (
    <div className={cx('skill-card-shell')}>
      <article
        aria-busy={interactionLocked || undefined}
        aria-disabled={interactionLocked || undefined}
        aria-expanded={userSkill ? active : undefined}
        className={cx(
          'skill-card',
          active && 'active',
          !userSkill && 'readonly',
          userSkill && 'has-controls',
          interactionLocked && 'pending'
        )}
        onClick={userSkill && !interactionLocked ? () => onOpen(userSkill) : undefined}
        onKeyDown={
          userSkill && !interactionLocked
            ? (event) => handleUserCardKeyDown(event, userSkill, onOpen)
            : undefined
        }
        role={userSkill ? 'button' : undefined}
        tabIndex={userSkill ? 0 : undefined}
        title={userSkill ? `编辑技能 ${skill.name}` : `内置技能 ${skill.name}`}
      >
        <div className={cx('skill-card-heading')}>
          <span className={cx('skill-card-icon')} aria-hidden="true">
            <AppstoreOutlined />
          </span>
          <div className={cx('skill-card-name')}>
            <Title level={5}>{skill.name}</Title>
            <Tag>{userSkill ? '用户技能' : '内置技能'}</Tag>
          </div>
        </div>
        <Paragraph className={cx('skill-card-description')}>{skill.description}</Paragraph>
        <dl className={cx('skill-card-meta')}>
          <div>
            <dt>目录</dt>
            <dd title={skill.relativePath}>{skill.directoryName}</dd>
          </div>
          <div>
            <dt>{userSkill ? '更新时间' : '来源'}</dt>
            <dd>{userSkill ? formatUpdatedAt(userSkill.updatedAt) : '后端内置'}</dd>
          </div>
          <div>
            <dt>版本</dt>
            <dd>{skill.version || '未标注'}</dd>
          </div>
        </dl>
      </article>
      {userSkill && (
        <div
          className={cx('skill-card-controls')}
          onClick={(event) => event.stopPropagation()}
        >
          <div className={cx('skill-card-switch')}>
            <Switch
              aria-label={`${userSkill.enabled ? '关闭' : '开启'}技能 ${userSkill.name}`}
              checked={userSkill.enabled}
              disabled={deleting}
              loading={toggling}
              onChange={(enabled) => onToggle(userSkill, enabled)}
              size="small"
            />
          </div>
          <Button
            aria-label={`删除技能 ${userSkill.name}`}
            className={cx('skill-card-delete-button')}
            danger
            disabled={toggling}
            icon={<DeleteOutlined />}
            loading={deleting}
            onClick={(event) => {
              event.stopPropagation()
              onDelete(userSkill)
            }}
            shape="circle"
            title={`删除技能 ${userSkill.name}`}
            type="text"
          />
        </div>
      )}
    </div>
  )
}

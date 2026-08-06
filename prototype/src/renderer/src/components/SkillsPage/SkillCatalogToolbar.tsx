import { SearchOutlined } from '@ant-design/icons'
import { Input, Segmented, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../utils'
import type { SkillCategory } from './skillCatalog'

const { Text } = Typography

type Props = {
  category: SkillCategory
  countLabel: string
  onCategoryChange: (category: SkillCategory) => void
  onQueryChange: (query: string) => void
  query: string
}

/** 渲染技能来源筛选、搜索框和当前结果数量。 */
export default function SkillCatalogToolbar({
  category,
  countLabel,
  onCategoryChange,
  onQueryChange,
  query
}: Props): ReactElement {
  return (
    <div className={cx('skills-toolbar')}>
      <Segmented
        aria-label="技能来源"
        onChange={(value) => onCategoryChange(value as SkillCategory)}
        options={[
          { label: '用户', value: 'user' },
          { label: '内置', value: 'builtin' }
        ]}
        value={category}
      />
      <Input
        allowClear
        aria-label="搜索技能"
        onChange={(event) => onQueryChange(event.target.value)}
        placeholder="按名称、描述或目录筛选"
        prefix={<SearchOutlined />}
        value={query}
      />
      <Text type="secondary">{countLabel}</Text>
    </div>
  )
}

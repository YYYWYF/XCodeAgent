import { Cascader, Select, Button } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import './index.less'

/** 应用API数据绑定链路的交互模式集合：类型/来源/缺失引导/映射绑定四张步骤卡共用同一套内嵌、等待与续跑判定。 */
export const API_BINDING_STEP_MODES: ReadonlyArray<string> = [
  'api_source_type',
  'api_source_select',
  'api_source_missing',
  'api_binding'
]

/** 数据来源类型的可选值：与实现类型对齐，决定后续映射绑定方式（模板 / 参数适配）。 */
export type ApiSourceTypeChoice = '数据库' | '外部服务'

/** 类型选择选项：标题 + 注释说明。 */
export type ApiSourceTypeOption = {
  key: ApiSourceTypeChoice
  label: string
  description: string
}

type TypeCardProps = {
  /** 应用API名，用于选项说明。 */
  objectName: string
  /** 类型选项：数据表 / 外部API 两类。 */
  options: ApiSourceTypeOption[]
  disabled?: boolean
  /** 提交中状态：等待续跑快照替换期间保持禁用。 */
  submitting?: boolean
  /** 选择某个类型后提交。 */
  onSelect: (kind: ApiSourceTypeChoice) => void
}

/**
 * 数据来源类型选择卡：应用API开发工作流第一个交互节点。
 * 用什么类型的数据源由用户自己判断——数据来源的信息只有用户自己知道，卡面不做任何建议；
 * 点击选项即提交。该卡作为工作流节点内嵌在节点轨迹中渲染，不单独成块。
 */
export function ApiSourceTypeCard({
  objectName,
  options,
  disabled = false,
  submitting = false,
  onSelect
}: TypeCardProps): ReactElement {
  return (
    <div aria-label="选择数据来源类型" className={cx('api-source-cards')} role="group">
      {options.map((option) => (
        <button
          key={option.key}
          type="button"
          className={cx('api-source-cards-row')}
          disabled={disabled || submitting}
          onClick={() => onSelect(option.key)}
        >
          <span className={cx('api-source-cards-copy')}>
            <strong>{option.label}</strong>
            <small>{option.description.replace('{api}', objectName)}</small>
          </span>
        </button>
      ))}
    </div>
  )
}

/** 级联的一段：一个数据库连接及其下已添加的数据表。 */
export type ApiSourceDatabaseGroup = {
  /** 连接显示名（如“回检业务库”）。 */
  connection: string
  /** 该连接下可绑定的表：绑定键 + 展示名（表名（说明））。 */
  tables: Array<{ key: string; label: string }>
}

/** 一个可绑定的外部接口：绑定键 + 展示名（接口名（签名））。 */
export type ApiSourceExternalOption = { key: string; label: string }

type SelectCardProps = {
  /** 已选定的来源类型：决定渲染级联（表）还是下拉（外部接口）。 */
  kind: ApiSourceTypeChoice
  /** 数据库级联选项：连接 → 表。 */
  databases: ApiSourceDatabaseGroup[]
  /** 外部接口下拉选项。 */
  externals: ApiSourceExternalOption[]
  disabled?: boolean
  submitting?: boolean
  /** 选定绑定对象后提交（值为绑定键）。 */
  onSelect: (key: string) => void
}

/**
 * 数据来源选择卡：一个表单项解决绑定对象的选择。
 * 数据表用连接 → 表的下拉级联（几十张表也只需两级定位），外部API用单层下拉；
 * 选到叶子即提交。该卡作为工作流节点内嵌在节点轨迹中渲染，不单独成块。
 */
export function ApiSourceSelectCard({
  kind,
  databases,
  externals,
  disabled = false,
  submitting = false,
  onSelect
}: SelectCardProps): ReactElement {
  if (kind !== '外部服务') {
    const options = databases.map((group) => ({
      value: group.connection,
      label: group.connection,
      children: group.tables.map((table) => ({ value: table.key, label: table.label }))
    }))
    return (
      <div aria-label="选择数据来源" className={cx('api-source-cards', 'form')} role="group">
        <label className={cx('api-source-cards-field')}>
          <span className={cx('api-source-cards-field-label')}>数据来源</span>
          <Cascader
            aria-label="选择要绑定的数据表"
            className={cx('api-source-cards-control')}
            disabled={disabled || submitting}
            expandTrigger="hover"
            options={options}
            placeholder="先选连接，再选要绑定的数据表"
            onChange={(value) => {
              const key = value[value.length - 1]
              if (typeof key === 'string') onSelect(key)
            }}
          />
        </label>
      </div>
    )
  }
  return (
    <div aria-label="选择数据来源" className={cx('api-source-cards', 'form')} role="group">
      <label className={cx('api-source-cards-field')}>
        <span className={cx('api-source-cards-field-label')}>数据来源</span>
        <Select
          aria-label="选择要绑定的外部接口"
          className={cx('api-source-cards-control')}
          disabled={disabled || submitting}
          loading={submitting}
          options={externals.map((item) => ({ value: item.key, label: item.label }))}
          placeholder="选择要绑定的外部接口"
          showSearch
          optionFilterProp="label"
          onChange={(value) => {
            if (typeof value === 'string') onSelect(value)
          }}
        />
      </label>
    </div>
  )
}

type MissingCardProps = {
  /** 缺失的来源类型显示名：数据表 / 外部API接口。 */
  kindLabel: string
  disabled?: boolean
  submitting?: boolean
  /** 用户表示已在对应抽屉中配置完成，重新检测可用来源。 */
  onRetry: () => void
}

/**
 * 来源缺失引导卡：目录中没有该类型的可用来源时不做选择，
 * 引导用户到左侧对应抽屉配置（数据表→数据源，外部API→外部API抽屉按域登记）；
 * 配置完成后一键重新检测继续旅程。该卡作为工作流节点内嵌在节点轨迹中渲染，不单独成块。
 */
export function ApiSourceMissingCard({
  kindLabel,
  disabled = false,
  submitting = false,
  onRetry
}: MissingCardProps): ReactElement {
  const isDatabase = kindLabel === '数据表'
  /** 打开左侧对应抽屉：由工作台页面监听事件后按缺失类型展开数据源或外部API抽屉。 */
  const openDataSources = (): void => {
    window.dispatchEvent(
      new CustomEvent('aistudio:prototype:open-data-sources', {
        detail: { kind: isDatabase ? 'database' : 'external' }
      })
    )
  }
  return (
    <div aria-label="数据来源待配置" className={cx('api-source-cards', 'missing')} role="group">
      <p className={cx('api-source-cards-missing-text')}>
        目录中还没有可绑定的{kindLabel}。请先打开左侧「{isDatabase ? '数据源' : '外部API'}」抽屉
        {isDatabase ? '连接数据库并添加数据表' : '在对应域下登记接口'}
        ，配置完成后再回来继续绑定。
      </p>
      <div className={cx('api-source-cards-missing-actions')}>
        <Button
          disabled={disabled || submitting}
          onClick={openDataSources}
        >
          打开{isDatabase ? '数据源' : '外部API'}
        </Button>
        <Button
          type="primary"
          disabled={disabled || submitting}
          loading={submitting}
          onClick={onRetry}
        >
          我已配置，重新检测
        </Button>
      </div>
    </div>
  )
}

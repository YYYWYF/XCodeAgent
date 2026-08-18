import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  DatabaseOutlined
} from '@ant-design/icons'
import { Table, Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useState } from 'react'
import type { DevelopmentPlanningEntityOption } from '../../../../typings'
import { cx } from '../../../../utils'
import { fetchDatabaseTableColumns } from '../../../../service/workspaceTools'
import './EntityInfoPanel.less'

const { Text } = Typography

type EntityInfoPanelProps = {
  entity: DevelopmentPlanningEntityOption | undefined
  theme: 'light' | 'dark'
  workspaceRoot?: string
}

const DATA_SOURCE_LABELS: Record<string, string> = {
  database: '数据库',
  external_api: '外部 API',
  static: '静态数据'
}

const FIELD_TYPE_LABELS: Record<string, string> = {
  text: '文本',
  long_text: '长文本',
  number: '数字',
  decimal: '小数',
  date: '日期',
  datetime: '日期时间',
  enum: '枚举',
  boolean: '布尔'
}

/** 把数组或文本规整为可展示的字符串列表。 */
function stringList(value: unknown): string[] {
  if (Array.isArray(value)) {
    return value
      .map((item) => {
        if (typeof item === 'string') return item.trim()
        if (item && typeof item === 'object') {
          const record = item as Record<string, unknown>
          return String(
            record.rule || record.text || record.description || record.relation || ''
          ).trim()
        }
        return ''
      })
      .filter(Boolean)
  }
  const text = typeof value === 'string' ? value.trim() : ''
  return text ? [text] : []
}

/** 展示单个实体的概要信息、字段清单与设计入口。 */
export default function EntityInfoPanel({
  entity,
  theme,
  workspaceRoot
}: EntityInfoPanelProps): ReactElement {
  const designed = Boolean(entity?.designed || entity?.hasDetailPlan)
  const fields = Array.isArray(entity?.fields) ? entity.fields : []
  const detail = entity?.detail
  const databaseDesign =
    detail?.database_design && typeof detail.database_design === 'object'
      ? detail.database_design
      : undefined
  const bindings = Array.isArray(databaseDesign?.bindings)
    ? databaseDesign.bindings.filter(
        (item): item is Record<string, unknown> => Boolean(item && typeof item === 'object')
      )
    : []
  const tableName =
    String(databaseDesign?.matched_table || detail?.table_design?.name || '').trim() || ''
  const databaseName = String(
    databaseDesign?.database_name || databaseDesign?.schema_context?.database || ''
  ).trim()
  const schemaTables = Array.isArray(databaseDesign?.schema_context?.tables)
    ? databaseDesign.schema_context.tables
    : []
  const matchedSchemaTable = tableName
    ? schemaTables.find(
        (table) => String(table.table_name || table.name || '') === tableName
      )
    : undefined
  const storedDbColumns =
    databaseDesign?.selected_table?.columns ||
    matchedSchemaTable?.columns ||
    detail?.table_design?.columns ||
    []
  // 数据库字段信息以实时查库为准：进入信息面板时按目标表重新读取真实列，
  // 查询失败或未配置连接时回退到已确认设计里保存的列结构。
  const [liveDbColumns, setLiveDbColumns] = useState<Array<Record<string, unknown>>>([])
  const [dbColumnsLoading, setDbColumnsLoading] = useState(false)
  const [dbColumnsError, setDbColumnsError] = useState('')
  const dataSourceType = entity?.dataSourceType || detail?.data_source_type || ''
  useEffect(() => {
    let active = true
    if (dataSourceType !== 'database' || !tableName || !workspaceRoot) {
      setLiveDbColumns([])
      setDbColumnsError('')
      setDbColumnsLoading(false)
      return undefined
    }
    setDbColumnsLoading(true)
    setDbColumnsError('')
    fetchDatabaseTableColumns({
      workspace_root: workspaceRoot,
      table_name: tableName
    })
      .then((result) => {
        if (!active) return
        setDbColumnsLoading(false)
        if (result.status === 'ok' && result.columns.length > 0) {
          setLiveDbColumns(result.columns)
        } else {
          setLiveDbColumns([])
          setDbColumnsError(result.message || '未能读取数据库表字段。')
        }
      })
      .catch((caughtError: unknown) => {
        if (!active) return
        setDbColumnsLoading(false)
        setLiveDbColumns([])
        setDbColumnsError(
          caughtError instanceof Error ? caughtError.message : '读取数据库表字段失败。'
        )
      })
    return () => {
      active = false
    }
  }, [dataSourceType, tableName, workspaceRoot])
  const dbColumns = liveDbColumns.length > 0 ? liveDbColumns : storedDbColumns
  const businessRules = stringList(detail?.business_rules)
  const relationships = stringList(detail?.relationships)
  const acceptanceCriteria = stringList(detail?.acceptance_criteria)
  const risks = stringList(detail?.risks)
  const externalApiDesign =
    detail?.external_api_design && typeof detail.external_api_design === 'object'
      ? detail.external_api_design
      : undefined
  const apiInfo =
    externalApiDesign?.api_info && typeof externalApiDesign.api_info === 'object'
      ? (externalApiDesign.api_info as Record<string, unknown>)
      : undefined
  const apiPath = String(apiInfo?.path || '')
  const apiMethod = String(apiInfo?.method || '')
  const apiMappings = Array.isArray(externalApiDesign?.field_mappings)
    ? externalApiDesign.field_mappings.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item && typeof item === 'object')
      )
    : []
  const staticDesign =
    detail?.static_design && typeof detail.static_design === 'object'
      ? detail.static_design
      : undefined
  const seedRows = Array.isArray(staticDesign?.seed_rows)
    ? staticDesign.seed_rows.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item && typeof item === 'object')
      )
    : []
  const seedRowKeys = Array.from(
    new Set(seedRows.flatMap((row) => Object.keys(row)))
  ).slice(0, 8)
  const fieldValueEntries =
    staticDesign?.field_values && typeof staticDesign.field_values === 'object'
      ? Object.entries(staticDesign.field_values).filter(
          (entry): entry is [string, unknown[]] => Array.isArray(entry[1])
        )
      : []
  const dataSourceLabel = dataSourceType
    ? DATA_SOURCE_LABELS[dataSourceType] || dataSourceType
    : '未选择'

  return (
    <div className={cx('entity-info-panel')} data-theme={theme}>
      <header className={cx('entity-info-head')}>
        <span className={cx('entity-info-icon')}>
          <DatabaseOutlined />
        </span>
        <div className={cx('entity-info-identity')}>
          <div className={cx('entity-info-title-row')}>
            <Text className={cx('entity-info-title')} strong>
              {entity?.label || '实体'}
            </Text>
            <Tag
              className={cx('entity-info-status', designed ? 'designed' : 'undesign')}
              color={designed ? 'green' : 'default'}
            >
              {designed ? <CheckCircleOutlined /> : <ClockCircleOutlined />}
              <span>{designed ? '已设计' : '未完成设计'}</span>
            </Tag>
          </div>
          <Text className={cx('entity-info-description')} type="secondary">
            {entity?.purpose || '暂无描述'}
          </Text>
        </div>
      </header>

      <div className={cx('entity-info-meta')}>
        <div className={cx('entity-info-meta-item')}>
          <Text type="secondary">实体 ID</Text>
          <Text code>{entity?.id || '-'}</Text>
        </div>
        <div className={cx('entity-info-meta-item')}>
          <Text type="secondary">数据源类型</Text>
          <Text strong>{dataSourceLabel}</Text>
        </div>
        {dataSourceType === 'database' && databaseName ? (
          <div className={cx('entity-info-meta-item')}>
            <Text type="secondary">数据库库名</Text>
            <Text code>{databaseName}</Text>
          </div>
        ) : null}
        {dataSourceType === 'database' && tableName ? (
          <div className={cx('entity-info-meta-item')}>
            <Text type="secondary">数据表</Text>
            <Text code>{tableName}</Text>
          </div>
        ) : null}
        {!detail ? (
          <div className={cx('entity-info-meta-item')}>
            <Text type="secondary">字段数量</Text>
            <Text strong>{fields.length}</Text>
          </div>
        ) : null}
      </div>

      {!detail ? (
        <section className={cx('entity-info-fields')}>
        <Text className={cx('entity-info-section-title')} strong>
          字段清单
        </Text>
        {fields.length > 0 ? (
          <Table
            className={cx('entity-info-fields-table')}
            columns={[
              {
                title: '字段名',
                dataIndex: 'name',
                key: 'name',
                render: (name: string, field: { label?: string }) => (
                  <span>
                    <Text code>{name}</Text>
                    {field.label ? (
                      <Text className={cx('entity-info-field-label')} type="secondary">
                        {field.label}
                      </Text>
                    ) : null}
                  </span>
                )
              },
              {
                title: '类型',
                dataIndex: 'type',
                key: 'type',
                render: (type: string) => FIELD_TYPE_LABELS[type] || type || '文本'
              },
              {
                title: '必填',
                dataIndex: 'required',
                key: 'required',
                width: 72,
                render: (required: boolean) => (required ? '是' : '否')
              }
            ]}
            dataSource={fields.map((field, index) => ({ ...field, key: field.name || index }))}
            pagination={false}
            rowKey="key"
            size="small"
          />
        ) : (
          <Text className={cx('entity-info-fields-empty')} type="secondary">
            项目计划尚未生成字段清单。
          </Text>
        )}
        </section>
      ) : null}

      {detail ? (
        <section className={cx('entity-info-design')}>
          <Text className={cx('entity-info-section-title')} strong>
            实体设计内容
          </Text>
          {bindings.length > 0 ? (
            <div className={cx('entity-info-design-block')}>
              <Text type="secondary">字段绑定</Text>
              <Table
                className={cx('entity-info-fields-table')}
                columns={[
                  {
                    title: '实体字段',
                    dataIndex: 'entity_field',
                    key: 'entity_field',
                    render: (name: string) => <Text code>{name || '-'}</Text>
                  },
                  {
                    title: '表字段',
                    dataIndex: 'table_column',
                    key: 'table_column',
                    render: (name: string) => <Text code>{name || '-'}</Text>
                  }
                ]}
                dataSource={bindings.map((item, index) => ({
                  ...item,
                  key: String(item.entity_field || index)
                }))}
                pagination={false}
                rowKey="key"
                size="small"
              />
            </div>
          ) : null}
          {dataSourceType === 'database' && tableName ? (
            <div className={cx('entity-info-design-block')}>
              <Text type="secondary">
                数据库字段信息（实时查询 · {tableName}）
              </Text>
              {dbColumnsLoading ? (
                <Text className={cx('entity-info-db-status')} type="secondary">
                  正在读取数据库表字段…
                </Text>
              ) : dbColumns.length > 0 ? (
                <Table
                  className={cx('entity-info-fields-table')}
                  columns={[
                    {
                      title: '列名',
                      dataIndex: 'name',
                      key: 'name',
                      render: (name: string) => <Text code>{name || '-'}</Text>
                    },
                    {
                      title: '类型',
                      dataIndex: 'type',
                      key: 'type',
                      render: (type: string) => type || '-'
                    },
                    {
                      title: '可空',
                      dataIndex: 'nullable',
                      key: 'nullable',
                      width: 64,
                      render: (nullable: boolean) => (nullable ? '是' : '否')
                    },
                    {
                      title: '说明',
                      dataIndex: 'comment',
                      key: 'comment',
                      render: (comment: string) => comment || '-'
                    }
                  ]}
                  dataSource={dbColumns.map((column, index) => ({
                    ...column,
                    key: String(column.name || index)
                  }))}
                  pagination={false}
                  rowKey="key"
                  size="small"
                />
              ) : (
                <Text className={cx('entity-info-db-status')} type="secondary">
                  {dbColumnsError || '未能读取该表的字段信息。'}
                </Text>
              )}
            </div>
          ) : null}
          {dataSourceType === 'external_api' && apiPath ? (
            <div className={cx('entity-info-design-block')}>
              <Text type="secondary">外部 API 信息</Text>
              <div className={cx('entity-info-meta')}>
                <div className={cx('entity-info-meta-item')}>
                  <Text type="secondary">请求方式</Text>
                  <Text code>{apiMethod || '-'}</Text>
                </div>
                <div className={cx('entity-info-meta-item')}>
                  <Text type="secondary">请求路径</Text>
                  <Text code>{apiPath}</Text>
                </div>
              </div>
            </div>
          ) : null}
          {dataSourceType === 'external_api' && apiMappings.length > 0 ? (
            <div className={cx('entity-info-design-block')}>
              <Text type="secondary">返回体字段映射</Text>
              <Table
                className={cx('entity-info-fields-table')}
                columns={[
                  {
                    title: '实体字段',
                    dataIndex: 'entity_field',
                    key: 'entity_field',
                    render: (name: string) => <Text code>{name || '-'}</Text>
                  },
                  {
                    title: '来源字段',
                    dataIndex: 'source_field',
                    key: 'source_field',
                    render: (name: string) => <Text code>{name || '-'}</Text>
                  }
                ]}
                dataSource={apiMappings.map((item, index) => ({
                  ...item,
                  key: String(item.entity_field || index)
                }))}
                pagination={false}
                rowKey="key"
                size="small"
              />
            </div>
          ) : null}
          {dataSourceType === 'static' && seedRows.length > 0 ? (
            <div className={cx('entity-info-design-block')}>
              <Text type="secondary">种子数据（{seedRows.length} 条）</Text>
              <Table
                className={cx('entity-info-fields-table')}
                columns={seedRowKeys.map((key) => ({
                  title: key,
                  dataIndex: key,
                  key,
                  render: (value: unknown) =>
                    value === undefined || value === null ? '-' : String(value)
                }))}
                dataSource={seedRows.slice(0, 8).map((row, index) => ({
                  ...row,
                  key: index
                }))}
                pagination={false}
                rowKey="key"
                size="small"
              />
              {seedRows.length > 8 ? (
                <Text className={cx('entity-info-db-status')} type="secondary">
                  仅展示前 8 条种子记录，共 {seedRows.length} 条。
                </Text>
              ) : null}
            </div>
          ) : null}
          {dataSourceType === 'static' && fieldValueEntries.length > 0 ? (
            <div className={cx('entity-info-design-block')}>
              <Text type="secondary">字段取值 / 枚举</Text>
              {fieldValueEntries.map(([key, values]) => (
                <div className={cx('entity-info-field-values-row')} key={key}>
                  <Text code>{key}</Text>
                  <div>
                    {values.map((value) => (
                      <Tag key={String(value)}>{String(value)}</Tag>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : null}
          {businessRules.length > 0 ? (
            <div className={cx('entity-info-design-block')}>
              <Text type="secondary">业务规则</Text>
              {businessRules.map((item) => (
                <Text key={item}>{item}</Text>
              ))}
            </div>
          ) : null}
          {relationships.length > 0 ? (
            <div className={cx('entity-info-design-block')}>
              <Text type="secondary">关系</Text>
              {relationships.map((item) => (
                <Text key={item}>{item}</Text>
              ))}
            </div>
          ) : null}
          {acceptanceCriteria.length > 0 ? (
            <div className={cx('entity-info-design-block')}>
              <Text type="secondary">验收标准</Text>
              {acceptanceCriteria.map((item) => (
                <Text key={item}>{item}</Text>
              ))}
            </div>
          ) : null}
          {risks.length > 0 ? (
            <div className={cx('entity-info-design-block')}>
              <Text type="secondary">风险</Text>
              {risks.map((item) => (
                <Text key={item}>{item}</Text>
              ))}
            </div>
          ) : null}
        </section>
      ) : null}
    </div>
  )
}

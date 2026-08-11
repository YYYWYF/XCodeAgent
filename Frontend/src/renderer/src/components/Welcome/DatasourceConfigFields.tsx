import { DatabaseOutlined } from '@ant-design/icons'
import { Form, Input, InputNumber, Radio } from 'antd'
import type { FormInstance, RadioChangeEvent } from 'antd'
import { DatasourceEnum, type ApplicationDraft, type DatasourceConnectionMode } from '../../typings'
import { datasourceTypeOptions } from './constants'
import { cx } from '../../utils'

type Props = {
  form: FormInstance<ApplicationDraft>
}

type ExternalDatabaseFieldsProps = {
  mode: DatasourceConnectionMode
}

/** 渲染两种外部数据库方案共用的连接字段，并按方案补充专属凭据。 */
function ExternalDatabaseFields({ mode }: ExternalDatabaseFieldsProps): JSX.Element {
  const configKey = mode === 'dbid' ? 'dbidMode' : 'plantMode'

  return (
    <div className={cx('application-form-grid', 'datasource-config-fields')}>
      <Form.Item
        label="数据库地址"
        name={['datasource', 'db', configKey, 'domain']}
        preserve={false}
        rules={[
          { required: true, message: '请输入数据库地址' },
          { whitespace: true, message: '数据库地址不能只包含空格' }
        ]}
      >
        <Input placeholder="例如：127.0.0.1" />
      </Form.Item>
      <Form.Item
        label="端口"
        name={['datasource', 'db', configKey, 'port']}
        preserve={false}
        rules={[
          { required: true, message: '请输入数据库端口' },
          {
            type: 'number',
            min: 1,
            max: 65535,
            message: '数据库端口必须是1到65535之间的整数'
          }
        ]}
      >
        <InputNumber
          className={cx('datasource-config-port')}
          max={65535}
          min={1}
          placeholder="例如：3306"
          precision={0}
        />
      </Form.Item>
      {mode === 'dbid' ? (
        <Form.Item
          label="DBID"
          name={['datasource', 'db', 'dbidMode', 'dbid']}
          preserve={false}
          rules={[
            { required: true, message: '请输入DBID' },
            { whitespace: true, message: 'DBID不能只包含空格' }
          ]}
        >
          <Input placeholder="请输入DBID" />
        </Form.Item>
      ) : null}
      <Form.Item
        label="用户名"
        name={['datasource', 'db', configKey, 'userName']}
        preserve={false}
        rules={[
          { required: true, message: '请输入数据库用户名' },
          { whitespace: true, message: '数据库用户名不能只包含空格' }
        ]}
      >
        <Input placeholder="请输入数据库用户名" />
      </Form.Item>
      {mode === 'plant' ? (
        <Form.Item
          label="密码"
          name={['datasource', 'db', 'plantMode', 'pwd']}
          preserve={false}
          rules={[
            { required: true, message: '请输入数据库密码' },
            { whitespace: true, message: '数据库密码不能只包含空格' }
          ]}
        >
          <Input.Password autoComplete="new-password" placeholder="请输入数据库密码" />
        </Form.Item>
      ) : null}
      <Form.Item
        label="Schema"
        name={['datasource', 'db', configKey, 'schema']}
        preserve={false}
        rules={[
          { required: true, message: '请输入数据库Schema' },
          { whitespace: true, message: '数据库Schema不能只包含空格' }
        ]}
      >
        <Input placeholder="请输入数据库Schema" />
      </Form.Item>
    </div>
  )
}

/** 清除数据库草稿和校验状态，保证 Static 不残留数据库字段。 */
function clearDatabaseDraft(form: FormInstance<ApplicationDraft>): void {
  form.resetFields([['datasource', 'db']])
  form.setFieldValue(['datasource', 'db'], undefined)
}

/** 渲染新建应用的数据源选择，并确保正式配置只保留受支持的连接方式。 */
export default function DatasourceConfigFields({ form }: Props): JSX.Element {
  const datasourceType =
    (Form.useWatch(['datasource', 'type'], form) as DatasourceEnum | undefined) ?? DatasourceEnum.DB
  const useBuiltin =
    (Form.useWatch(['datasource', 'db', 'useBuiltin'], form) as boolean | undefined) ?? false
  const connectionMode = Form.useWatch(['datasource', 'db', 'connectionMode'], form) as
    | DatasourceConnectionMode
    | undefined

  /** 切换数据源类型 */
  const handleDatasourceTypeChange = (event: RadioChangeEvent): void => {
    const nextType = event.target.value as DatasourceEnum

    form.setFieldValue(['datasource', 'type'], nextType)
    clearDatabaseDraft(form)
    if (nextType === DatasourceEnum.DB) {
      form.setFieldValue(['datasource', 'db'], {
        useBuiltin: false,
        connectionMode: 'plant'
      })
    }
  }

  /** 切换模拟或外部数据库时清除不再适用的连接草稿。 */
  const handleDatabaseTypeChange = (event: RadioChangeEvent): void => {
    const nextUseBuiltin = event.target.value as boolean

    form.setFieldValue(['datasource', 'db', 'useBuiltin'], nextUseBuiltin)
    form.setFieldValue(['datasource', 'db', 'plantMode'], undefined)
    form.setFieldValue(['datasource', 'db', 'dbidMode'], undefined)
    form.setFieldValue(['datasource', 'db', 'connectionMode'], nextUseBuiltin ? undefined : 'plant')
  }

  /** 切换外部连接方案时清除另一方案的数据和校验状态。 */
  const handleConnectionModeChange = (event: RadioChangeEvent): void => {
    const nextMode = event.target.value as DatasourceConnectionMode
    const inactiveField = nextMode === 'dbid' ? 'plantMode' : 'dbidMode'
    form.setFieldValue(['datasource', 'db', inactiveField], undefined)
  }

  return (
    <section className={cx('application-form-section', 'application-form-section--full')}>
      <div className={cx('application-form-section-title', 'datasource-config-title')}>
        <span className={cx('application-form-section-icon')}>
          <DatabaseOutlined />
        </span>
        <span className={cx('application-form-section-text')}>数据源配置</span>
      </div>

      <Form.Item label="数据源类型" name={['datasource', 'type']}>
        <Radio.Group
          buttonStyle="solid"
          className={cx('datasource-type-picker')}
          onChange={handleDatasourceTypeChange}
        >
          {datasourceTypeOptions.map((option) => (
            <Radio.Button disabled={option.disabled} key={option.value} value={option.value}>
              <span className={cx('datasource-type-option')}>
                <span className={cx('datasource-type-label')}>{option.label}</span>
                <span className={cx('datasource-type-description')}>{option.description}</span>
              </span>
            </Radio.Button>
          ))}
        </Radio.Group>
      </Form.Item>

      {datasourceType === DatasourceEnum.DB ? (
        <>
          <Form.Item label="数据库类型" name={['datasource', 'db', 'useBuiltin']} preserve={false}>
            <Radio.Group buttonStyle="solid" onChange={handleDatabaseTypeChange}>
              <Radio.Button value={true}>模拟数据库</Radio.Button>
              <Radio.Button value={false}>外部数据库</Radio.Button>
            </Radio.Group>
          </Form.Item>

          {!useBuiltin ? (
            <>
              <Form.Item
                label="数据库连接方案"
                name={['datasource', 'db', 'connectionMode']}
                preserve={false}
                rules={[{ required: true, message: '请选择数据库连接方案' }]}
              >
                <Radio.Group buttonStyle="solid" onChange={handleConnectionModeChange}>
                  <Radio.Button value="dbid">通过DBID连接</Radio.Button>
                  <Radio.Button value="plant">通过账号密码连接</Radio.Button>
                </Radio.Group>
              </Form.Item>

              {connectionMode ? <ExternalDatabaseFields mode={connectionMode} /> : null}
            </>
          ) : null}
        </>
      ) : null}
    </section>
  )
}

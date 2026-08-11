import { DatabaseOutlined } from '@ant-design/icons'
import { Form, Input, InputNumber, Radio } from 'antd'
import type { FormInstance, RadioChangeEvent } from 'antd'
import {
  type ApplicationDraft,
  type DatasourceConnectionMode
} from '../../typings'
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
      >
        <Input placeholder="例如：127.0.0.1" />
      </Form.Item>
      <Form.Item
        label="端口"
        name={['datasource', 'db', configKey, 'port']}
        preserve={false}
        rules={[
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
        >
          <Input placeholder="请输入DBID" />
        </Form.Item>
      ) : null}
      <Form.Item
        label="用户名"
        name={['datasource', 'db', configKey, 'userName']}
        preserve={false}
      >
        <Input placeholder="请输入数据库用户名" />
      </Form.Item>
      {mode === 'plant' ? (
        <Form.Item
          label="密码"
          name={['datasource', 'db', 'plantMode', 'pwd']}
          preserve={false}
        >
          <Input.Password autoComplete="new-password" placeholder="请输入数据库密码" />
        </Form.Item>
      ) : null}
      <Form.Item
        label="Schema"
        name={['datasource', 'db', configKey, 'schema']}
        preserve={false}
      >
        <Input placeholder="请输入数据库Schema" />
      </Form.Item>
    </div>
  )
}

/** 渲染数据库连接配置；创建应用不选数据源类型，实体数据源类型在项目规划阶段按实体选择。 */
export default function DatasourceConfigFields({ form }: Props): JSX.Element {
  const useBuiltin =
    (Form.useWatch(['datasource', 'db', 'useBuiltin'], form) as boolean | undefined) ?? false
  const connectionMode = Form.useWatch(['datasource', 'db', 'connectionMode'], form) as
    | DatasourceConnectionMode
    | undefined

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
          >
            <Radio.Group buttonStyle="solid" onChange={handleConnectionModeChange}>
              <Radio.Button value="dbid">通过DBID连接</Radio.Button>
              <Radio.Button value="plant">通过账号密码连接</Radio.Button>
            </Radio.Group>
          </Form.Item>

          {connectionMode ? <ExternalDatabaseFields mode={connectionMode} /> : null}
        </>
      ) : null}
    </section>
  )
}

import { DatabaseOutlined } from '@ant-design/icons'
import { Form, Input, InputNumber, Radio } from 'antd'
import type { FormInstance, RadioChangeEvent } from 'antd'
import type { ApplicationDraft, DatasourceConnectionMode } from '../../typings'
import { cx } from '../../utils'

type Props = {
  form: FormInstance<ApplicationDraft>
}

/** 渲染新建应用的数据源选择，并确保两种外部数据库配置保持互斥。 */
export default function DatasourceConfigFields({ form }: Props): JSX.Element {
  const useBuiltin = Form.useWatch(['datasource', 'db', 'useBuiltin'], form) ?? true
  const connectionMode = Form.useWatch(['datasource', 'db', 'connectionMode'], form) as
    | DatasourceConnectionMode
    | undefined

  /** 切换数据库类型时清除不再生效的外部连接配置。 */
  const handleDatabaseTypeChange = (event: RadioChangeEvent): void => {
    if (event.target.value !== true) return
    form.setFieldValue(['datasource', 'db', 'connectionMode'], undefined)
    form.setFieldValue(['datasource', 'db', 'dbidMode'], undefined)
    form.setFieldValue(['datasource', 'db', 'plantMode'], undefined)
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
        <span className={cx('application-form-section-text')}>数据库配置</span>
      </div>

      <Form.Item hidden name={['datasource', 'type']}>
        <Input />
      </Form.Item>

      <Form.Item label="数据库类型" name={['datasource', 'db', 'useBuiltin']}>
        <Radio.Group buttonStyle="solid" onChange={handleDatabaseTypeChange}>
          <Radio.Button value={true}>平台内置数据库</Radio.Button>
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

          {connectionMode === 'dbid' ? (
            <div className={cx('application-form-grid', 'datasource-config-fields')}>
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
              <Form.Item
                label="用户名"
                name={['datasource', 'db', 'dbidMode', 'userName']}
                preserve={false}
                rules={[
                  { required: true, message: '请输入数据库用户名' },
                  { whitespace: true, message: '数据库用户名不能只包含空格' }
                ]}
              >
                <Input placeholder="请输入数据库用户名" />
              </Form.Item>
            </div>
          ) : null}

          {connectionMode === 'plant' ? (
            <div className={cx('application-form-grid', 'datasource-config-fields')}>
              <Form.Item
                label="数据库地址"
                name={['datasource', 'db', 'plantMode', 'domain']}
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
                name={['datasource', 'db', 'plantMode', 'port']}
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
              <Form.Item
                label="用户名"
                name={['datasource', 'db', 'plantMode', 'userName']}
                preserve={false}
                rules={[
                  { required: true, message: '请输入数据库用户名' },
                  { whitespace: true, message: '数据库用户名不能只包含空格' }
                ]}
              >
                <Input placeholder="请输入数据库用户名" />
              </Form.Item>
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
              <Form.Item
                label="Schema"
                name={['datasource', 'db', 'plantMode', 'schema']}
                preserve={false}
                rules={[
                  { required: true, message: '请输入数据库Schema' },
                  { whitespace: true, message: '数据库Schema不能只包含空格' }
                ]}
              >
                <Input placeholder="请输入数据库Schema" />
              </Form.Item>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  )
}

import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server.node'
import { Form } from 'antd'
import ApplicationForm from '../src/renderer/src/components/Welcome/ApplicationForm'
import { initialApplicationDraft } from '../src/renderer/src/components/Welcome/constants'
import type { ApplicationDraft } from '../src/renderer/src/typings'

test('ApplicationForm 实际集成：新建有辅助入口，设置只读且没有辅助入口', () => {
  /** 服务端静态渲染真实表单，核对两种业务模式的集成参数。 */
  function ApplicationHost({ mode }: { mode: 'create' | 'settings' }): ReactElement {
    const [form] = Form.useForm<ApplicationDraft>()
    return <ApplicationForm form={form} mode={mode} initialValues={{ ...initialApplicationDraft, senario: '已有应用需求' }} />
  }
  const createMarkup = renderToStaticMarkup(<ApplicationHost mode="create" />)
  const settingsMarkup = renderToStaticMarkup(<ApplicationHost mode="settings" />)
  assert.match(createMarkup, /放大编辑/)
  assert.match(createMarkup, /应用需求描述/)
  assert.match(settingsMarkup, /readonly=""/i)
  assert.match(settingsMarkup, /已有应用需求/)
  for (const label of ['填写指引', '插入需求模板', '+ 添加页面', '放大编辑']) assert.ok(!settingsMarkup.includes(label))
})

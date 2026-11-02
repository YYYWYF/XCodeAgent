import assert from 'node:assert/strict'
import { test } from 'node:test'
import { useState, type ReactElement } from 'react'
import { act, create, type ReactTestRenderer, type ReactTestInstance } from 'react-test-renderer'
import { Form, Popover, Tooltip, type FormInstance } from 'antd'
import RequirementDescriptionEditor from '../src/renderer/src/components/RequirementDescriptionEditor'
import { FULL_REQUIREMENTS_TEMPLATE, PAGE_REQUIREMENTS_TEMPLATE } from '../src/renderer/src/constants/applicationRequirements'
import { initialApplicationDraft } from '../src/renderer/src/components/Welcome/constants'
import { buildApplicationSchema } from '../src/renderer/src/components/Welcome/utils'
import type { ApplicationDraft } from '../src/renderer/src/typings'

/** 按实际可见按钮文本定位并触发操作。 */
function click(root: ReactTestRenderer, label: string): void {
  const buttons = root.root.findAllByType('button').filter((node) => node.children.join('') === label)
  assert.equal(buttons.length, 1, `应存在唯一可见按钮：${label}`)
  act(() => buttons[0].props.onClick())
}

/** 读取主输入框或放大编辑框，保留真实组件的受控更新链路。 */
function input(root: ReactTestRenderer, expanded = false): ReactTestInstance {
  return root.root.findAllByType('textarea').find((node) => node.props['aria-label'] === (expanded ? '放大编辑需求内容' : '应用需求描述'))!
}

/** 按浮层标题定位确认和取消操作。 */
function modal(root: ReactTestRenderer, title: string): ReactTestInstance {
  return root.root.findAllByType('section').find((node) => node.props.title === title)!
}

test('受控编辑器支持安全插入、连续页面、常驻指引及可取消的放大编辑', () => {
  let root!: ReactTestRenderer
  let setExternal!: (value: string) => void
  /** 由父组件持有唯一正式需求值，测试所有编辑操作的回填。 */
  function Host(): ReactElement {
    const [value, setValue] = useState('')
    setExternal = setValue
    return <RequirementDescriptionEditor value={value} onChange={setValue} />
  }
  act(() => { root = create(<Host />) })
  try {
    click(root, '插入需求模板')
    assert.equal(input(root).props.value, FULL_REQUIREMENTS_TEMPLATE)
    act(() => input(root).props.onChange({ target: { value: '用户需求\n\n\n' } }))
    click(root, '插入需求模板')
    assert.equal(input(root).props.value, '用户需求\n\n\n')
    click(root, '取消')
    assert.equal(input(root).props.value, '用户需求\n\n\n')
    click(root, '插入需求模板')
    click(root, '追加模板')
    assert.equal(input(root).props.value, `用户需求\n\n\n${FULL_REQUIREMENTS_TEMPLATE}`)
    click(root, '插入需求模板')
    click(root, '替换内容')
    assert.equal(input(root).props.value, FULL_REQUIREMENTS_TEMPLATE)
    click(root, '+ 添加页面')
    click(root, '+ 添加页面')
    const pages = `${FULL_REQUIREMENTS_TEMPLATE}\n\n${PAGE_REQUIREMENTS_TEMPLATE}\n\n${PAGE_REQUIREMENTS_TEMPLATE}`
    assert.equal(input(root).props.value, pages)
    const guide = root.root.findByType(Popover)
    assert.deepEqual(guide.props.trigger, ['hover', 'focus'])
    assert.ok(guide.props.content)
    assert.equal(guide.findByType('button').props.onClick, undefined)
    const operationHints = root.root.findAllByType(Tooltip)
    assert.equal(operationHints.length, 2)
    assert.match(operationHints[0].props.title, /追加或替换/)
    assert.match(operationHints[1].props.title, /保留已填写/)
    assert.equal(input(root).props.value, pages)
    click(root, '放大编辑')
    act(() => input(root, true).props.onChange({ target: { value: '未确认内容' } }))
    assert.equal(input(root).props.value, pages)
    act(() => modal(root, '放大编辑应用需求描述').props.onCancel())
    assert.equal(input(root).props.value, pages)
    click(root, '放大编辑')
    act(() => input(root, true).props.onChange({ target: { value: '最终需求' } }))
    // 放大弹窗内的操作只更新临时草稿，确认前不得泄漏到正式值。
    const expanded = modal(root, '放大编辑应用需求描述')
    act(() => expanded.findAllByType('button').find((node) => node.children.join('') === '插入需求模板')!.props.onClick())
    click(root, '取消')
    assert.equal(input(root, true).props.value, '最终需求')
    act(() => expanded.findAllByType('button').find((node) => node.children.join('') === '插入需求模板')!.props.onClick())
    click(root, '追加模板')
    assert.equal(input(root, true).props.value, `最终需求\n\n${FULL_REQUIREMENTS_TEMPLATE}`)
    assert.equal(input(root).props.value, pages)
    act(() => input(root, true).props.onChange({ target: { value: '最终需求' } }))
    act(() => expanded.findAllByType('button').find((node) => node.children.join('') === '+ 添加页面')!.props.onClick())
    assert.equal(input(root).props.value, pages)
    assert.equal(input(root, true).props.value, `最终需求\n\n${PAGE_REQUIREMENTS_TEMPLATE}`)
    act(() => modal(root, '放大编辑应用需求描述').props.onOk())
    assert.equal(input(root).props.value, `最终需求\n\n${PAGE_REQUIREMENTS_TEMPLATE}`)
    act(() => setExternal('父层更新'))
    assert.equal(input(root).props.value, '父层更新')
    for (const button of root.root.findAllByType('button')) assert.equal(button.props.type, 'button')
  } finally { act(() => root.unmount()) }
})

test('只读与禁用模式隐藏全部操作，且不能通过输入回调修改内容', () => {
  for (const props of [{ readOnly: true }, { disabled: true }]) {
    let changes = 0, root!: ReactTestRenderer
    act(() => { root = create(<RequirementDescriptionEditor value="原需求" {...props} onChange={() => { changes++ }} />) })
    try {
      assert.equal(root.root.findAllByType('button').length, 0)
      act(() => input(root).props.onChange({ target: { value: '改动' } }))
      assert.equal(changes, 0)
      assert.equal(input(root).props.value, '原需求')
      assert.equal(input(root).props.readOnly, !!props.readOnly)
      assert.equal(input(root).props.disabled, !!props.disabled)
    } finally { act(() => root.unmount()) }
  }
})

test('真实 Form.Item 保持必填校验并将最终需求交给现有创建配置转换', async () => {
  let form!: FormInstance<ApplicationDraft>, root!: ReactTestRenderer
  const previousWindow = globalThis.window
  // Ant Design 的响应式订阅需要 matchMedia；测试不模拟 Form 存储或校验。
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {
    matchMedia: () => ({ matches: false, addListener() {}, removeListener() {} })
  } })
  /** 按创建页相同方式将编辑器注册到 senario 字段。 */
  function FormHost(): ReactElement {
    ;[form] = Form.useForm<ApplicationDraft>()
    return <Form form={form} initialValues={{ ...initialApplicationDraft, senario: '' }}>
      <Form.Item name="senario" rules={[{ required: true, whitespace: true, message: '请输入应用需求描述' }]}>
        <RequirementDescriptionEditor />
      </Form.Item>
    </Form>
  }
  try {
    await act(async () => { root = create(<FormHost />) })
    await act(async () => { await assert.rejects(form.validateFields(['senario'])) })
    act(() => input(root).props.onChange({ target: { value: '   ' } }))
    await act(async () => { await assert.rejects(form.validateFields(['senario'])) })
    click(root, '插入需求模板')
    click(root, '放大编辑')
    act(() => input(root, true).props.onChange({ target: { value: '创建时提交的最终需求' } }))
    act(() => modal(root, '放大编辑应用需求描述').props.onOk())
    await act(async () => { await form.validateFields(['senario']) })
    assert.equal(form.getFieldValue('senario'), '创建时提交的最终需求')
    assert.equal(buildApplicationSchema({ ...initialApplicationDraft, ...form.getFieldsValue(true) }).senario, '创建时提交的最终需求')
    act(() => form.setFieldsValue({ senario: '父表单回填' }))
    assert.equal(input(root).props.value, '父表单回填')
  } finally {
    act(() => root?.unmount())
    Object.defineProperty(globalThis, 'window', { configurable: true, value: previousWindow })
  }
})


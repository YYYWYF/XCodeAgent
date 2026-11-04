import assert from 'node:assert/strict'
import { test } from 'node:test'
import { act, create, type ReactTestRenderer, type ReactTestInstance } from 'react-test-renderer'
import DatabaseDeliveryPreview from '../src/renderer/src/components/DatabaseDeliveryPreview'
import WorkbenchVersionModals from '../src/renderer/src/components/WorkbenchVersionModals'
import {
  databaseDeliveryPaths,
  readDeliveryFile
} from '../src/renderer/src/components/DatabaseDeliveryPreview/databaseDeliveryPreview'
import type { ReadWorkspaceFileResult } from '../src/renderer/src/service/workspaceTools'

/** 构造与现有文件读取接口一致的响应。 */
function page(
  content: string,
  extra: Partial<ReadWorkspaceFileResult> = {}
): ReadWorkspaceFileResult {
  return {
    tool: 'file.read',
    path: 'test.sql',
    start_line: 1,
    end_line: content ? 1 : 0,
    total_lines: content ? 1 : 0,
    sha256: 'digest',
    content,
    truncated: false,
    ...extra
  }
}

/** 在真实组件外围替换文件传输，记录所有调用以排除生成、执行或历史读取。 */
function mockFiles(read: (path: string) => Promise<ReadWorkspaceFileResult>): {
  calls: Record<string, unknown>[]
  restore: () => void
} {
  const original = globalThis.fetch
  const calls: Record<string, unknown>[] = []
  globalThis.fetch = async (url, init) => {
    assert.match(String(url), /\/tools\/file\/read$/)
    const request = JSON.parse(String(init?.body))
    calls.push(request)
    assert.equal(request.revision, undefined)
    const result = await read(request.path)
    return { ok: true, json: async () => result } as Response
  }
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: {
      devAgentStudio: {
        clipboard: {
          writeText: async (text: string) => {
            copied = text
          }
        }
      }
    }
  })
  return {
    calls,
    restore: () => {
      globalThis.fetch = original
    }
  }
}
let copied = ''

/** 断言可见 SQL 文本，忽略高亮产生的安全 span 标签。 */
function sqlText(root: ReactTestRenderer): string {
  return root.root
    .findByType('pre')
    .findByType('code')
    .props.dangerouslySetInnerHTML.__html.replace(/<[^>]*>/g, '')
}

/** 通过按钮的可见文字触发真实事件。 */
function button(root: ReactTestRenderer, text: string) {
  return root.root
    .findAllByType('button')
    .find((item) => (item.props['aria-label'] || visibleText(item)).includes(text))!
}

/** 递归提取测试渲染树文本，忽略装饰图标与事件对象。 */
function visibleText(node: ReactTestInstance | string): string {
  return typeof node === 'string' ? node : node.children.map(visibleText).join('')
}

test('真实分支层级与后端路径一致，拒绝空白、非法分支和点目录', () => {
  assert.deepEqual(databaseDeliveryPaths('release/v1_1'), {
    'create.sql': 'backend/db/release/v1_1/create.sql',
    'alter.sql': 'backend/db/release/v1_1/alter.sql'
  })
  for (const name of ['', ' v1', '../v1', 'release/./v1', 'release//v1', 'HEAD'])
    assert.throws(() => databaseDeliveryPaths(name))
})

test('分页重读截断的边界行，保留缩进换行，空文件合法', async () => {
  const starts: number[] = []
  const result = await readDeliveryFile('/app', 'create.sql', async (request) => {
    starts.push(request.start_line!)
    return request.start_line === 1
      ? page('CREATE\n  par', { end_line: 2, total_lines: 3, truncated: true })
      : page('  partial;\nEND', { start_line: 2, end_line: 3, total_lines: 3 })
  })
  assert.deepEqual(starts, [1, 2])
  assert.deepEqual(result, { content: 'CREATE\n  partial;\nEND' })
  assert.deepEqual(await readDeliveryFile('/app', 'alter.sql', async () => page('')), {
    content: ''
  })
})

test('字符截断、文件中途变化、读取错误与安全上限均阻止就绪', async () => {
  assert.match(
    (await readDeliveryFile('/app', 'x', async () => page('partial', { truncated: true }))).error!,
    /截断/
  )
  let count = 0
  assert.match(
    (
      await readDeliveryFile('/app', 'x', async () =>
        ++count === 1
          ? page('a\nb', { end_line: 2, total_lines: 2, truncated: true })
          : page('b', { start_line: 2, end_line: 2, total_lines: 2, sha256: 'changed' })
      )
    ).error!,
    /变化/
  )
  assert.match(
    (
      await readDeliveryFile('/app', 'x', async () => {
        throw new Error('404 missing')
      })
    ).error!,
    /404/
  )
  let start = 1
  assert.match(
    (
      await readDeliveryFile('/app', 'x', async () => {
        const current = start++
        return page('a'.repeat(199998) + '\nb', {
          start_line: current,
          end_line: current + 1,
          total_lines: 100,
          truncated: true
        })
      })
    ).error!,
    /200 万/
  )
})

test('独立文件卡片默认收起，分别展开、复制与重新获取，空增量禁用复制', async () => {
  const sql = ['role', 'resource', 'role_resource', 'user_role']
    .map((name) => `CREATE TABLE \`12ab_${name}\` (id bigint);`)
    .join('\n')
  const mock = mockFiles(async (path) => page(path.endsWith('create.sql') ? sql : ''))
  const ready: boolean[] = []
  let root!: ReactTestRenderer
  try {
    await act(async () => {
      root = create(
        <DatabaseDeliveryPreview
          workspaceRoot="/app"
          branchName="release/v1_1"
          onReadyChange={(value) => ready.push(value)}
        />
      )
    })
    assert.deepEqual(
      root.root
        .findAllByType('code')
        .slice(0, 2)
        .map((item) => item.children[0]),
      Object.values(databaseDeliveryPaths('release/v1_1'))
    )
    assert.equal(root.root.findAllByType('pre').length, 0)
    assert.equal(button(root, '展开 create.sql').props['aria-expanded'], false)
    act(() => button(root, '展开 create.sql').props.onClick())
    assert.equal(button(root, '收起 create.sql').props['aria-expanded'], true)
    assert.equal(root.root.findByType('pre').props['aria-label'], 'create.sql')
    const cards = root.root.findAllByType('article')
    assert.match(visibleText(cards[0]), /已读取/)
    assert.match(visibleText(cards[1]), /无增量/)
    assert.equal(cards[1].findAllByType('button')[0].props.disabled, true)
    for (const name of ['role', 'resource', 'role_resource', 'user_role'])
      assert.match(sqlText(root), new RegExp(`12ab_${name}`))
    await act(async () => {
      button(root, '复制 SQL').props.onClick()
    })
    assert.equal(copied, sql)
    act(() => button(root, '展开 alter.sql').props.onClick())
    assert.equal(root.root.findAllByType('article').length, 2)
    assert.equal(button(root, '展开 alter.sql'), undefined)
    assert.match(visibleText(root.root), /当前版本无数据库增量变更/)
    act(() => button(root, '收起 create.sql').props.onClick())
    assert.equal(root.root.findAllByType('pre').length, 0)
    act(() => button(root, '展开 create.sql').props.onClick())
    assert.equal(mock.calls.length, 2)
    await act(async () => {
      button(root, '重新获取').props.onClick()
    })
    assert.equal(mock.calls.length, 3)
    assert.equal(mock.calls[2].path, 'backend/db/release/v1_1/create.sql')
    assert.equal(button(root, '展开 create.sql').props['aria-expanded'], false)
    assert.equal(ready.at(-1), true)
    assert.equal(ready.at(-2), false)
  } finally {
    act(() => root.unmount())
    mock.restore()
  }
})

test('更换工作区或版本后旧响应不覆盖新预览', async () => {
  const pending: ((result: ReadWorkspaceFileResult) => void)[] = []
  const mock = mockFiles(() => new Promise((resolve) => pending.push(resolve)))
  const ready: boolean[] = []
  let root!: ReactTestRenderer
  try {
    await act(async () => {
      root = create(
        <DatabaseDeliveryPreview
          workspaceRoot="/a"
          branchName="v1"
          onReadyChange={(value) => ready.push(value)}
        />
      )
    })
    await act(async () => {
      root.update(
        <DatabaseDeliveryPreview
          workspaceRoot="/b"
          branchName="v2"
          onReadyChange={(value) => ready.push(value)}
        />
      )
    })
    await act(async () => {
      pending[2](page('new SQL'))
      pending[3](page(''))
    })
    await act(async () => {
      pending[0](page('old SQL'))
      pending[1](page('old alter'))
    })
    assert.equal(root.root.findAllByType('pre').length, 0)
    act(() => button(root, '展开 create.sql').props.onClick())
    assert.equal(sqlText(root), 'new SQL')
    assert.equal(ready.at(-1), true)
    assert.equal(ready.filter(Boolean).length, 1)
  } finally {
    act(() => root.unmount())
    mock.restore()
  }
})

test('Builtin 完整读取后即可提交，无确认勾选；重开与换版本默认收起，其他数据源沿用原行为', async () => {
  const mock = mockFiles(async (path) =>
    page(path.endsWith('create.sql') ? 'CREATE TABLE business (id bigint);' : '')
  )
  let generated = 0,
    root!: ReactTestRenderer
  // 不触发迭代流程，其余参数由组件默认的关闭状态处理。
  const props = {
    publishBranchName: 'v1',
    publishDescription: '业务变更',
    onGenerate: () => {
      generated++
    },
    application: {
      workspaceRoot: '/app',
      datasource: { type: 'database', db: { useBuiltin: true } }
    }
  } as unknown as Parameters<typeof WorkbenchVersionModals>[0]
  try {
    act(() => {
      root = create(<WorkbenchVersionModals {...props} />)
    })
    assert.equal(button(root, '确认提交').props.disabled, true)
    await act(async () => {})
    assert.equal(root.root.findAllByType('input').length, 0)
    assert.doesNotMatch(visibleText(root.root), /已确认数据库交付脚本/)
    assert.equal(button(root, '确认提交').props.disabled, false)
    act(() => button(root, '确认提交').props.onClick())
    assert.equal(generated, 1)
    await act(async () => {
      button(root, '重新获取').props.onClick()
    })
    assert.equal(button(root, '确认提交').props.disabled, false)
    act(() => button(root, '展开 create.sql').props.onClick())
    await act(async () => {
      root.update(<WorkbenchVersionModals {...props} publishBranchName="release/v2" />)
    })
    assert.equal(button(root, '展开 create.sql').props['aria-expanded'], false)
    assert.equal(button(root, '确认提交').props.disabled, false)
    act(() => button(root, '展开 create.sql').props.onClick())
    await act(async () => {
      root.update(<WorkbenchVersionModals {...props} publishBranchName={undefined} />)
    })
    await act(async () => {
      root.update(<WorkbenchVersionModals {...props} />)
    })
    assert.equal(button(root, '展开 create.sql').props['aria-expanded'], false)
    assert.equal(button(root, '确认提交').props.disabled, false)
    for (const datasource of [
      { type: 'database', db: { useBuiltin: false, mode: 'direct' } },
      { type: 'database', db: { useBuiltin: false, mode: 'dbid' } },
      { type: 'static' },
      { type: 'external_api' }
    ]) {
      const count = mock.calls.length
      await act(async () => {
        root.update(
          <WorkbenchVersionModals
            {...props}
            application={{ ...props.application, datasource } as typeof props.application}
          />
        )
      })
      assert.equal(root.root.findAllByType(DatabaseDeliveryPreview).length, 0)
      assert.equal(mock.calls.length, count)
      assert.equal(button(root, '确认提交').props.disabled, false)
    }
  } finally {
    act(() => root.unmount())
    mock.restore()
  }
})

test('缺失文件与未解决截断保留具体错误并禁用发布', async () => {
  for (const failure of ['missing', 'truncated']) {
    const mock = mockFiles(async (path) => {
      if (path.endsWith('create.sql')) return page('business DDL')
      if (failure === 'missing') throw new Error('alter.sql missing')
      return page('partial', { truncated: true })
    })
    let root!: ReactTestRenderer
    const props = {
      publishBranchName: 'v1',
      publishDescription: '变更',
      application: {
        workspaceRoot: '/app',
        datasource: { type: 'database', db: { useBuiltin: true } }
      }
    } as unknown as Parameters<typeof WorkbenchVersionModals>[0]
    try {
      await act(async () => {
        root = create(<WorkbenchVersionModals {...props} />)
      })
      assert.equal(button(root, '确认提交').props.disabled, true)
      assert.equal(root.root.findAllByType('input').length, 0)
      assert.match(visibleText(root.root), /alter.sql.*无法完整读取/)
    } finally {
      act(() => root.unmount())
      mock.restore()
    }
  }
})

test('单独重新获取增量时保留完整建表预览，读取期间禁用提交，失败后可重试', async () => {
  let resolveReload!: (result: ReadWorkspaceFileResult) => void
  let alterReads = 0
  const mock = mockFiles(async (path) => {
    if (path.endsWith('create.sql')) return page('CREATE TABLE business (id bigint);')
    alterReads++
    if (alterReads === 2)
      return new Promise((resolve) => {
        resolveReload = resolve
      })
    return page('')
  })
  const props = {
    publishBranchName: 'v1',
    publishDescription: '变更',
    application: {
      workspaceRoot: '/app',
      datasource: { type: 'database', db: { useBuiltin: true } }
    }
  } as unknown as Parameters<typeof WorkbenchVersionModals>[0]
  let root!: ReactTestRenderer
  /** 精确定位增量卡片，避免误触完整建表的同名操作。 */
  function reloadAlter(): void {
    const card = root.root
      .findAllByType('article')
      .find((item) => item.props['aria-label'].endsWith('alter.sql'))!
    card
      .findAllByType('button')
      .find((item) => visibleText(item).includes('重新获取'))!
      .props.onClick()
  }
  try {
    await act(async () => {
      root = create(<WorkbenchVersionModals {...props} />)
    })
    act(() => button(root, '展开 create.sql').props.onClick())
    await act(async () => {
      reloadAlter()
    })
    assert.equal(button(root, '确认提交').props.disabled, true)
    assert.match(sqlText(root), /CREATE TABLE business/)
    assert.equal(mock.calls.length, 3)
    assert.equal(mock.calls[2].path, 'backend/db/v1/alter.sql')
    await act(async () => {
      resolveReload(page('', { start_line: 99 }))
    })
    assert.equal(button(root, '确认提交').props.disabled, true)
    assert.match(visibleText(root.root), /读取失败/)
    await act(async () => {
      reloadAlter()
    })
    assert.equal(button(root, '确认提交').props.disabled, false)
    assert.equal(mock.calls.length, 4)
    assert.match(sqlText(root), /CREATE TABLE business/)
  } finally {
    act(() => root.unmount())
    mock.restore()
  }
})

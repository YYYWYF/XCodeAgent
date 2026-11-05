import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement, type ComponentProps } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { useSourceFileNavigation } from '../src/renderer/src/components/AiChatPanel/components/SourcePanel/useSourceFileNavigation'

/** 只挂载真实文件导航 Hook，避免引入文件内容和目录的网络请求。 */
function Harness(props: Parameters<typeof useSourceFileNavigation>[0]): null {
  useSourceFileNavigation(props)
  return null
}

test('显式文件不依赖初始目录快照；目录就绪后展开深层祖先', async () => {
  const file = 'agent-runtime/src/app/persistence/checkpointer.py'
  let selected = ''
  let expanded = new Set<string>()
  const directories: string[] = []
  const props: ComponentProps<typeof Harness> = {
    workspaceRoot: '/workspace', requestedFilePath: file, treeReady: false,
    onSelect: (path) => { selected = path },
    loadDirectory: async (path) => { directories.push(path) },
    setExpanded: (value) => { expanded = typeof value === 'function' ? value(expanded) : value }
  }
  let renderer: ReactTestRenderer
  await act(async () => { renderer = create(createElement(Harness, props)) })
  assert.equal(selected, file)
  assert.deepEqual(directories, [])
  await act(async () => { renderer!.update(createElement(Harness, { ...props, treeReady: true })) })
  assert.deepEqual(directories, ['agent-runtime', 'agent-runtime/src', 'agent-runtime/src/app', 'agent-runtime/src/app/persistence'])
  assert.deepEqual([...expanded], ['.', ...directories])
  await act(async () => { renderer!.unmount() })
})

test('切换目标后读取新文件，卸载后停止旧目录展开', async () => {
  const selected: string[] = []
  const directories: string[] = []
  let release: () => void = () => undefined
  const pending = new Promise<void>((resolve) => { release = resolve })
  const props: ComponentProps<typeof Harness> = {
    workspaceRoot: '/workspace', requestedFilePath: 'agent-runtime/src/app/agent/factory.py', treeReady: true,
    onSelect: (path) => { selected.push(path) },
    loadDirectory: async (path) => { directories.push(path); await pending },
    setExpanded: () => undefined
  }
  let renderer: ReactTestRenderer
  await act(async () => { renderer = create(createElement(Harness, props)) })
  await act(async () => { renderer!.update(createElement(Harness, {
    ...props, requestedFilePath: 'frontend/src/App.tsx', treeReady: false
  })) })
  assert.deepEqual(selected, ['agent-runtime/src/app/agent/factory.py', 'frontend/src/App.tsx'])
  await act(async () => { renderer!.unmount(); release(); await pending })
  assert.deepEqual(directories, ['agent-runtime'])
})

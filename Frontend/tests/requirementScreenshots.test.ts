import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'
import { test } from 'node:test'
import {
  MAX_REQUIREMENT_SCREENSHOTS,
  readWorkspaceRequirementInput,
  selectRequirementScreenshotMetadata,
  stageWorkspaceRequirementInput
} from '../src/main/requirementScreenshots'

const PNG_HEADER = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])

/** 在临时目录生成满足桌面端文件头校验的测试 PNG。 */
async function writeTestPng(root: string, name = 'screen.png'): Promise<string> {
  const file = path.join(root, name)
  await fs.writeFile(file, Buffer.concat([PNG_HEADER, Buffer.from('test-image-content')]))
  return file
}

/** 验证截图被复制到工作区，且独立清单可通过摘要复核后恢复。 */
test('复制并恢复截图需求输入', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-screenshot-input-'))
  try {
    const sourceRoot = path.join(root, 'source')
    const workspaceRoot = path.join(root, 'workspace')
    await fs.mkdir(sourceRoot)
    await fs.mkdir(workspaceRoot)
    const source = await writeTestPng(sourceRoot)
    const selected = await selectRequirementScreenshotMetadata([source])
    assert.equal(selected.length, 1)
    const staged = await stageWorkspaceRequirementInput(workspaceRoot, [source])
    assert.equal(staged.mode, 'screenshot')
    assert.match(staged.screenshots[0].relativePath, /^\.xcodeagent\/inputs\/screenshots\//)
    assert.deepEqual(await readWorkspaceRequirementInput(workspaceRoot), staged)
  } finally {
    await fs.rm(root, { force: true, recursive: true })
  }
})

/** 验证任意 1–10 张截图均可保留完整清单，超过上限则在入口明确拒绝。 */
test('支持一到十张任意数量的截图', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-screenshot-count-'))
  try {
    const sources = await Promise.all(
      Array.from({ length: MAX_REQUIREMENT_SCREENSHOTS + 1 }, (_, index) =>
        writeTestPng(root, `screen-${index + 1}.png`)
      )
    )
    for (let count = 1; count <= MAX_REQUIREMENT_SCREENSHOTS; count += 1) {
      const selected = await selectRequirementScreenshotMetadata(sources.slice(0, count))
      assert.equal(selected.length, count)
      assert.deepEqual(
        selected.map((item) => item.name),
        sources.slice(0, count).map((source) => path.basename(source))
      )
    }
    await assert.rejects(
      selectRequirementScreenshotMetadata(sources),
      /不能超过 10 张/
    )
  } finally {
    await fs.rm(root, { force: true, recursive: true })
  }
})

/** 验证扩展名不能绕过真实图片文件头校验。 */
test('拒绝伪造图片扩展名', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-screenshot-invalid-'))
  try {
    const source = path.join(root, 'fake.png')
    await fs.writeFile(source, 'not-an-image')
    await assert.rejects(selectRequirementScreenshotMetadata([source]), /仅支持真实的/)
  } finally {
    await fs.rm(root, { force: true, recursive: true })
  }
})

/** 验证恢复清单不能借助相对路径读取截图根目录之外的文件。 */
test('拒绝越界的工作区截图清单', async () => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'xcodeagent-screenshot-boundary-'))
  try {
    const workspaceRoot = path.join(root, 'workspace')
    const source = await writeTestPng(root, 'outside.png')
    await fs.mkdir(workspaceRoot)
    const staged = await stageWorkspaceRequirementInput(workspaceRoot, [source])
    const manifestPath = path.join(
      workspaceRoot,
      '.xcodeagent',
      'inputs',
      'screenshots',
      'requirement-input.json'
    )
    staged.screenshots[0].relativePath = '../outside.png'
    await fs.writeFile(manifestPath, `${JSON.stringify(staged)}\n`, 'utf8')
    await assert.rejects(readWorkspaceRequirementInput(workspaceRoot), /越界|不存在/)
  } finally {
    await fs.rm(root, { force: true, recursive: true })
  }
})

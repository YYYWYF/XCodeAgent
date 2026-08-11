import fs from 'node:fs/promises'

/** 判断文件系统错误是否仅表示目标路径已经不存在。 */
export function isFileSystemNotFoundError(error: unknown): boolean {
  return Boolean(
    error && typeof error === 'object' && (error as { code?: unknown }).code === 'ENOENT'
  )
}

/** 读取目标路径的元信息；目标不存在时返回 undefined，其他错误继续抛出。 */
export async function lstatIfPresent(
  targetPath: string
): Promise<Awaited<ReturnType<typeof fs.lstat>> | undefined> {
  try {
    return await fs.lstat(targetPath)
  } catch (error) {
    if (isFileSystemNotFoundError(error)) return undefined
    throw error
  }
}

/** 递归删除目录；目标在删除前或删除过程中消失时按幂等成功处理。 */
export async function removeDirectoryIfPresent(targetPath: string): Promise<void> {
  try {
    await fs.rm(targetPath, {
      force: false,
      maxRetries: 3,
      recursive: true,
      retryDelay: 150
    })
  } catch (error) {
    if (isFileSystemNotFoundError(error)) return
    throw error
  }
}

/** 将存在的路径移入系统回收站；目标已不存在时按幂等成功处理。 */
export async function movePathToTrashIfPresent(
  targetPath: string,
  trashItem: (path: string) => Promise<void>
): Promise<void> {
  if (!(await lstatIfPresent(targetPath))) return

  try {
    await trashItem(targetPath)
  } catch (error) {
    // 路径可能在检查后被其他进程移除，此时仍视为目标已经清理完成。
    if (isFileSystemNotFoundError(error)) return
    throw error
  }
}

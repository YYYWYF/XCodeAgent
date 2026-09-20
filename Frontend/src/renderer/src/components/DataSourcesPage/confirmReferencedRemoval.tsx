import { confirmWorkspaceAction } from '../workspaceDialogs'
import { message } from 'antd'
import type { ModalFuncProps } from 'antd'
import { requestSourceReferences } from '../../service/dataSources'

/** 删除目录资源前读取真实引用；查询失败时不进入删除确认。 */
export function confirmReferencedRemoval(workspaceRoot: string, sourceId: string,
  target: { directoryId?: string; operationId?: string }, options: ModalFuncProps): void {
  void requestSourceReferences(workspaceRoot, sourceId, target).then((references) => {
    confirmWorkspaceAction({ ...options, content: <>{options.content}<p>{references.length ? `关联映射：${references.join('、')}。正式映射不会自动删除。` : '暂无正式映射引用。'}</p></> })
  }).catch((reason) => message.error(reason instanceof Error ? reason.message : '读取引用失败，请重试。'))
}

import { Input, Modal, Select, Switch } from 'antd'
import type { BusinessObject, BusinessOperation } from './model'

type Props = {
  editor: 'object' | 'field' | 'operation' | 'binding' | null
  editIndex: number | null
  object: BusinessObject
  operation?: BusinessOperation
  name: string
  description: string
  fieldType: string
  required: boolean
  duplicate: boolean
  inputs: string[]
  outputs: string[]
  pages: string[]
  setEditor: (value: null) => void
  setName: (value: string) => void
  setDescription: (value: string) => void
  setFieldType: (value: string) => void
  setRequired: (value: boolean) => void
  setInputs: (value: string[]) => void
  setOutputs: (value: string[]) => void
  setPages: (value: string[]) => void
  saveEditor: () => void
}

/** 在原位浮层编辑实体、字段、操作和页面关联的草稿。 */
export default function BusinessObjectEditor({
  editor,
  editIndex,
  object,
  operation,
  name,
  description,
  fieldType,
  required,
  duplicate,
  inputs,
  outputs,
  pages,
  setEditor,
  setName,
  setDescription,
  setFieldType,
  setRequired,
  setInputs,
  setOutputs,
  setPages,
  saveEditor
}: Props): JSX.Element {
  return (
    <Modal
      getContainer={false}
      className="bo-modal"
      open={editor !== null}
      title={
        editor === 'object'
          ? '新建实体'
          : editor === 'binding'
            ? '关联使用页面'
            : `${editIndex === null ? '添加' : '编辑'}${editor === 'field' ? '业务字段' : '业务操作'}`
      }
      onCancel={() => setEditor(null)}
      onOk={saveEditor}
      okText="保存草稿"
      cancelText="取消"
      okButtonProps={{
        disabled:
          editor !== 'binding' &&
          (!name.trim() || duplicate || (editor === 'operation' && !outputs.length))
      }}
      destroyOnClose
    >
      {editor === 'binding' ? (
        <>
          <p className="bo-muted">
            页面只使用「{object.name}.{operation?.name}()」，无需选择数据库表或服务接口。
          </p>
          <Select
            aria-label="关联页面"
            mode="tags"
            style={{ width: '100%' }}
            value={pages}
            onChange={setPages}
            tokenSeparators={['，', ',']}
            options={['我的回检', '回检详情', '回检填报', '客户详情', '客户列表'].map((value) => ({
              value,
              label: value
            }))}
          />
        </>
      ) : (
        <>
          <label>名称</label>
          <Input
            aria-label="名称"
            value={name}
            maxLength={30}
            onChange={(event) => setName(event.target.value)}
          />
          {duplicate && <p className="bo-pending">名称已存在，请使用其他名称。</p>}
          {editor === 'field' ? (
            <>
              <label>字段类型</label>
              <Select
                aria-label="字段类型"
                value={fieldType}
                onChange={setFieldType}
                options={['文本', '数字', '金额', '枚举', '日期时间', '布尔'].map((value) => ({
                  value,
                  label: value
                }))}
              />
              <label>
                必填 <Switch checked={required} onChange={setRequired} />
              </label>
            </>
          ) : (
            <>
              <label>业务说明</label>
              <Input.TextArea
                aria-label="业务说明"
                value={description}
                rows={3}
                onChange={(event) => setDescription(event.target.value)}
              />
              {editor === 'object' ? (
                <p className="bo-muted">
                  只有具备独立编号、状态或生命周期的业务概念才需要新建实体。组合查询通常属于已有实体的操作。
                </p>
              ) : (
                <>
                  <label>操作输入</label>
                  <Select
                    aria-label="操作输入"
                    mode="tags"
                    value={inputs}
                    onChange={setInputs}
                    tokenSeparators={['，', ',']}
                  />
                  <label>返回结果</label>
                  <Select
                    aria-label="返回结果"
                    mode="tags"
                    value={outputs}
                    onChange={setOutputs}
                    options={object.fields.map((field) => ({
                      value: field.name,
                      label: field.name
                    }))}
                    tokenSeparators={['，', ',']}
                  />
                  <p className="bo-muted">可直接输入附加结果字段，不会因此创建新的实体。</p>
                </>
              )}
            </>
          )}
        </>
      )}
    </Modal>
  )
}

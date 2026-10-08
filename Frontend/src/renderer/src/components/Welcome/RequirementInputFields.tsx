import { PictureOutlined } from '@ant-design/icons'
import { Button, Form, Radio, Tag } from 'antd'
import type { FormInstance } from 'antd'
import type { ReactElement } from 'react'
import type {
  ApplicationDraft,
  ApplicationRequirementScreenshotSelection
} from '../../typings'
import { cx } from '../../utils'
import './RequirementInputFields.less'

type Props = {
  form: FormInstance<ApplicationDraft>
  onRemoveScreenshot: (path: string) => void
  onSelectScreenshots: () => void
  screenshots: ApplicationRequirementScreenshotSelection[]
  selecting: boolean
}

/** 把截图字节数转换为创建表单使用的简短容量文本。 */
function screenshotSizeLabel(size: number): string {
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

/** 渲染文字与截图需求输入切换，以及截图选择结果和约束说明。 */
export default function RequirementInputFields({
  form,
  onRemoveScreenshot,
  onSelectScreenshots,
  screenshots,
  selecting
}: Props): ReactElement {
  const mode = Form.useWatch('requirementInputMode', form) ?? 'text'
  return (
    <>
      <Form.Item label="需求生成方式" name="requirementInputMode">
        <Radio.Group>
          <Radio.Button value="text">根据文字描述生成</Radio.Button>
          <Radio.Button value="screenshot">根据页面截图生成</Radio.Button>
        </Radio.Group>
      </Form.Item>
      {mode === 'screenshot' ? (
        <Form.Item
          extra="支持 JPG、PNG、WebP，最多 10 张且单张不超过 15 MB；图片会复制到新应用工作区。"
          label="页面截图"
          required
        >
          <div className={cx('requirement-screenshot-picker')}>
            <Button
              disabled={screenshots.length >= 10}
              icon={<PictureOutlined />}
              loading={selecting}
              onClick={onSelectScreenshots}
            >
              选择截图
            </Button>
            <div className={cx('requirement-screenshot-list')}>
              {screenshots.map((screenshot) => (
                <Tag
                  closable
                  key={screenshot.path}
                  onClose={() => onRemoveScreenshot(screenshot.path)}
                >
                  {screenshot.name} · {screenshotSizeLabel(screenshot.size)}
                </Tag>
              ))}
              {!screenshots.length ? (
                <span className={cx('requirement-screenshot-empty')}>尚未选择截图</span>
              ) : null}
            </div>
          </div>
        </Form.Item>
      ) : null}
    </>
  )
}

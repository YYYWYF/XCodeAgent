import { Form, Input } from 'antd'
import type { FormInstance } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../utils'

/** 创建、设置与对话配置卡共用的主题色预设，避免不同入口产生不同蓝色色号。 */
export const themeColorPresets = [
  { label: '默认紫', value: '#6b3cf0' },
  { label: '招商红', value: '#e60012' },
  { label: '蓝色', value: '#1677ff' }
] as const

/** 渲染受控的主题色字段，并校验用户输入为完整 HEX 色值。 */
export default function ThemeColorField({ form }: { form: FormInstance }): ReactElement {
  const value = Form.useWatch(['theme', 'primaryColor'], form) ?? '#6b3cf0'
  const colorValue = /^#[0-9a-fA-F]{6}$/.test(value) ? value : '#6b3cf0'
  /** 将颜色写回已注册字段，供父级统一提交完整草稿。 */
  const apply = (color: string): void => form.setFields([{ name: ['theme', 'primaryColor'], value: color }])
  return (
    <Form.Item name={['theme', 'primaryColor']} rules={[{ required: true, message: '请选择主题色' }, { pattern: /^#[0-9a-fA-F]{6}$/, message: '主题色必须是 #RRGGBB 格式' }]}>
      <div className={cx('theme-color-picker-row')}>
        <div className={cx('theme-color-presets')} role="group" aria-label="预设主题色">
          {themeColorPresets.map((preset) => <button aria-pressed={value.toLowerCase() === preset.value} className={cx('theme-color-preset')} key={preset.value} onClick={() => apply(preset.value)} title={preset.label} type="button"><span aria-hidden="true" className={cx('theme-color-preset-swatch')} style={{ backgroundColor: preset.value }} /></button>)}
        </div>
        <span className={cx('theme-color-custom-label')}>自定义</span>
        <div className={cx('theme-color-swatch-wrapper')}>
          <div
            aria-hidden="true"
            className={cx('theme-color-swatch')}
            style={{ backgroundColor: colorValue }}
          />
          <input
            aria-label="自定义主题色"
            className={cx('theme-color-input')}
            onChange={(event) => apply(event.target.value)}
            type="color"
            value={colorValue}
          />
        </div>
        <Input
          aria-label="主题色色值"
          onChange={(event) => apply(event.target.value)}
          placeholder="自定义主题色色值，如：#6b3cf0"
          style={{ flex: 1 }}
          value={value}
        />
      </div>
    </Form.Item>
  )
}

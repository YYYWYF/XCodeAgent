# Mock 数据生成规范

Mock 文件统一放在 `src/apis/<ModuleName>.mock.ts`，用 mockjs 生成 20 条随机数据。

## 基本写法

```ts
import Mock from 'mockjs';
import type { ModuleItem } from '@/typings/Module';

const { Random } = Mock;

export const mockList: ModuleItem[] = Array.from({ length: 20 }, (_, i) => ({
  // 字段...
}));
```

要点：
- `import Mock from 'mockjs'`，`const { Random } = Mock`
- 用 `Array.from({ length: 20 }, (_, i) => ({ ... }))` 生成 20 条
- 每个字段对应 typings 里的类型，枚举字段值要与页面下拉选项一致

## Random 方法速查

**只能使用下表中的真实方法，不要编造方法名。** 最常见的编造错误是地区方法加了 `c` 前缀（`cprovince`/`ccity` 不存在）。

| 类别 | 方法 | 说明 |
|------|------|------|
| 中文姓名 | `Random.cname()` | 中文姓名 |
| 中文标题 | `Random.ctitle(min, max)` | 中文标题（字数范围） |
| 中文句子 | `Random.csentence(min, max)` | 中文句子 |
| 中文段落 | `Random.cparagraph(min, max)` | 中文段落 |
| 中文词 | `Random.cword(pool?, min, max)` | 中文词；`Random.cword(5,15)` 生成 5-15 字 |
| 地区 | `Random.province()` | 省份（**不是 cprovince**） |
| 地区 | `Random.city(prefix?)` | 城市；`Random.city(true)` 返回"省 市" |
| 地区 | `Random.county(prefix?)` | 区县 |
| 地区 | `Random.region()` | 区域（如华北） |
| 数字 | `Random.integer(min, max)` | 整数 |
| 数字 | `Random.float(min, max, dmin, dmax)` | 浮点数；`Random.float(2,15,0,2)` 生成 2-15 保留 2 位 |
| 数字 | `Random.natural(min, max)` | 自然数（≥0） |
| 文本 | `Random.string(pool, min, max)` | 字符串；`Random.string('0123456789', 18)` 生成 18 位数字串 |
| 文本 | `Random.word(min, max)` | 英文词 |
| 日期 | `Random.date('yyyy-MM-dd')` | 日期 |
| 日期 | `Random.datetime('yyyy-MM-dd HH:mm:ss')` | 日期时间 |
| 网络 | `Random.email()` | 邮箱 |
| 网络 | `Random.url()` | URL |
| 网络 | `Random.ip()` | IP |
| 选择 | `Random.pick(arr)` | 从数组随机取一个 |
| 选择 | `Random.shuffle(arr)` | 打乱数组 |
| 布尔 | `Random.boolean()` | true/false |
| 颜色 | `Random.color()` | 颜色值 |

## 完整示例

```ts
import Mock from 'mockjs';
const { Random } = Mock;

export const mockList = Array.from({ length: 20 }, (_, i) => ({
  id: `ID${Random.string('0123456789', 6)}`,
  name: Random.cname(),
  phone: `1${Random.string('3456789', 1)}${Random.string('0123456789', 9)}`,
  address: Random.province() + Random.city(true) + Random.cword(5, 15),
  amount: Random.float(1000, 100000, 0, 2),
  date: Random.date('yyyy-MM-dd'),
  status: Random.pick(['pending', 'approved', 'rejected']),
  remark: Random.pick(['', '', Random.csentence(5, 20)]),
}));
```

## 要点与常见错误

- **地区方法没有 `c` 前缀**：是 `province()`/`city()`/`county()`，不是 `cprovince()`/`ccity()`。这是最常编造的错误，会导致运行时 `Random.xxx is not a function`。
- 枚举字段用 `Random.pick(数组)`，不要用 `Random.integer` 取下标再取值。
- 手机号用 `1${Random.string('3456789',1)}${Random.string('0123456789',9)}` 拼接，保证格式合法。
- 可空字段用 `Random.pick(['', '', Random.csentence(...)])` 让空值占多数。
- 生成后逐个核对：每个 `Random.xxx` 是否在上表内、枚举值是否与页面 options 一致。

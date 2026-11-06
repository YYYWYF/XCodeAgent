// 泛化入口不能安全推断发布环境，要求调用者选择明确的环境命令。
console.error(
  'Choose an explicit package environment: build:mac:<dev|st|uat|prd> or build:unpack:<dev|st|uat|prd>.'
)
process.exit(1)

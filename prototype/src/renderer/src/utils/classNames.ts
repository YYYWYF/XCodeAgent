import styleConfig from '../config/style.json';

type ClassNamePart = string | false | null | undefined;

// 项目自定义类名的唯一前缀来源。需要换前缀时只修改 style.json。
export const CLASS_PREFIX = styleConfig.classPrefix;

/**
 * 为业务类名统一添加前缀，并自动忽略 false、null、undefined。
 * 示例：cx('button', selected && 'active') => "xa-button xa-active"
 */
export function cx(...classNames: ClassNamePart[]) {
  return classNames
    .filter((className): className is string => Boolean(className))
    .map((className) => `${CLASS_PREFIX}-${className}`)
    .join(' ');
}

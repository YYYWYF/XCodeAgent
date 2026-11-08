/** 为真实组件的无 DOM 挂载提供宿主类型、事件及图标样式容器，退出后还原所有全局对象。 */
export function installRendererDom(): () => void {
  type StyleNode = { tagName: string; parentNode: unknown; innerHTML: string;
    setAttribute: (name: string, value: string) => void; getAttribute: (name: string) => string | null }
  const head = {
    children: [] as StyleNode[],
    /** 图标库通过首节点保持样式的插入顺序。 */
    get firstChild(): StyleNode | undefined { return this.children[0] },
    /** 只挂载图标库的样式，不代替 React 宿主交互。 */
    appendChild(node: StyleNode): StyleNode { this.children.push(node); node.parentNode = this; return node },
    /** 在已知节点前插入样式。 */
    insertBefore(node: StyleNode, before?: StyleNode): StyleNode {
      const index = before ? this.children.indexOf(before) : -1
      this.children.splice(index < 0 ? this.children.length : index, 0, node); node.parentNode = this; return node
    },
    /** 清理样式库为确定容器创建的临时节点。 */
    removeChild(node: StyleNode): void {
      const index = this.children.indexOf(node)
      if (index >= 0) this.children.splice(index, 1)
      node.parentNode = null
    }
  }
  const document = Object.assign(new EventTarget(), {
    hidden: false, visibilityState: 'visible', head, body: head,
    querySelector: () => head,
    contains: (node: unknown) => node === head || head.children.includes(node as StyleNode),
    /** 用属性表实现样式去重需要的最小节点能力。 */
    createElement: (tag: string): StyleNode => {
      const attributes = new Map<string, string>()
      return { tagName: tag.toUpperCase(), parentNode: null, innerHTML: '',
        setAttribute: (name, value) => { attributes.set(name, value) },
        getAttribute: (name) => attributes.get(name) || null }
    }
  })
  const globals = { Element: class {}, HTMLElement: class {}, SVGElement: class {}, ShadowRoot: class {},
    document, window: Object.assign(new EventTarget(), { document, setTimeout, clearTimeout }) }
  const saved = new Map<string, PropertyDescriptor | undefined>()
  for (const [name, value] of Object.entries(globals)) {
    saved.set(name, Object.getOwnPropertyDescriptor(globalThis, name))
    Object.defineProperty(globalThis, name, { configurable: true, value })
  }
  /** 精确还原测试前的宿主，避免影响其他组件和源码读取的共享订阅。 */
  return (): void => {
    for (const [name, descriptor] of saved) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor)
      else Reflect.deleteProperty(globalThis, name)
    }
  }
}

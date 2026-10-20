export type BuildTaskInstruction = { number?: number; content: string }

/** 按连续编号拆分任务说明，保留原文，避免把版本号或代码中的小数当成步骤。 */
export function splitBuildTaskInstructions(description: string): BuildTaskInstruction[] {
  const text = description.trim()
  if (!text) return []
  const markers = [...text.matchAll(/(?:^|\s)(\d{1,3})[.．、)）](?:\s+|(?=[\u4e00-\u9fff]))/g)]
  const steps: RegExpMatchArray[] = []
  for (const marker of markers) {
    if (Number(marker[1]) === steps.length + 1) steps.push(marker)
  }
  if (steps.length === 0) return [{ content: text }]
  const prefix = text.slice(0, steps[0].index).trim()
  const instructions: BuildTaskInstruction[] = prefix ? [{ content: prefix }] : []
  steps.forEach((step, index) => {
    const content = text.slice(step.index! + step[0].length, steps[index + 1]?.index).trim()
    if (content) instructions.push({ number: Number(step[1]), content })
  })
  return instructions
}

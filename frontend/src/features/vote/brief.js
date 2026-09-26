// The one-page brief as plain text (Markdown). It is built from the SAME object the printed view renders
// (proposal.brief, assembled by the backend), so the download and the page always agree.

const line = (it) => (it.url ? `${it.text} (${it.url})` : it.text)

export function briefToMarkdown(b) {
  const out = [`# ${b.title}`, '']
  if (b.subtitle) out.push(b.subtitle, '')
  out.push(`Prepared ${b.generated} with Overload. ${b.credit || ''}`.trim(), '')
  for (const s of b.sections || []) {
    out.push(`## ${s.heading}`, '')
    for (const p of s.paragraphs || []) out.push(p, '')
    for (const it of s.items || []) {
      out.push(`- ${line(it)}`)
      if (it.detail) out.push(`  - ${it.detail}`)
    }
    if (s.items?.length) out.push('')
    if (s.note) out.push(`> ${s.note}`, '')
  }
  if (b.sources?.length) {
    out.push('## Sources', '')
    for (const s of b.sources) out.push(`- ${s.title}: ${s.url}`)
    out.push('')
  }
  out.push('---', '', b.disclaimer, '')
  return out.join('\n')
}

// Save text as a file in the browser (no server round trip).
export function downloadText(filename, text) {
  const url = URL.createObjectURL(new Blob([text], { type: 'text/markdown;charset=utf-8' }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  window.setTimeout(() => URL.revokeObjectURL(url), 1000)
}

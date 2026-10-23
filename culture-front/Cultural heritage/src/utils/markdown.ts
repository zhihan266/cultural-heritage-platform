import { h, type VNodeChild } from 'vue'

// Render text as Vue nodes. Raw model HTML is never inserted into the document.
function inline(text: string): VNodeChild[] {
  const pattern = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\([^\s)]+\))/g
  const nodes: VNodeChild[] = []
  let offset = 0
  for (const match of text.matchAll(pattern)) {
    nodes.push(text.slice(offset, match.index))
    const token = match[0]
    if (token.startsWith('`')) nodes.push(h('code', token.slice(1, -1)))
    else if (token.startsWith('**')) nodes.push(h('strong', token.slice(2, -2)))
    else if (token.startsWith('*')) nodes.push(h('em', token.slice(1, -1)))
    else {
      const link = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(token)
      if (link && /^https?:\/\//i.test(link[2] || ''))
        nodes.push(h('a', { href: link[2], target: '_blank', rel: 'noopener noreferrer' }, link[1]))
      else nodes.push(token)
    }
    offset = (match.index || 0) + token.length
  }
  nodes.push(text.slice(offset))
  return nodes
}
export function markdownNodes(text: string): VNodeChild[] {
  const lines = text.replace(/\r\n?/g, '\n').split('\n')
  const nodes: VNodeChild[] = []
  let i = 0
  while (i < lines.length) {
    const line = lines[i] || ''
    if (!line.trim()) {
      i++
      continue
    }
    if (/^\s*```/.test(line)) {
      const code: string[] = []
      i++
      while (i < lines.length && !/^\s*```/.test(lines[i] || '')) code.push(lines[i++] || '')
      i++
      nodes.push(h('pre', [h('code', code.join('\n'))]))
    } else if (/^#{1,6}\s/.test(line)) {
      const level = Math.min(line.indexOf(' '), 4)
      nodes.push(h(`h${level}`, inline(line.replace(/^#+\s/, ''))))
      i++
    } else if (/^\s*[-*+]\s/.test(line) || /^\s*\d+\.\s/.test(line)) {
      const ordered = /^\s*\d+\./.test(line)
      const items: VNodeChild[] = []
      const pattern = ordered ? /^\s*\d+\.\s/ : /^\s*[-*+]\s/
      while (i < lines.length && pattern.test(lines[i] || ''))
        items.push(h('li', inline((lines[i++] || '').replace(pattern, ''))))
      nodes.push(h(ordered ? 'ol' : 'ul', items))
    } else if (/^>\s?/.test(line)) {
      const quote: string[] = []
      while (i < lines.length && /^>\s?/.test(lines[i] || ''))
        quote.push((lines[i++] || '').replace(/^>\s?/, ''))
      nodes.push(h('blockquote', inline(quote.join('\n'))))
    } else if (line.includes('|') && /^\s*\|?\s*:?-+/.test(lines[i + 1] || '')) {
      const cells = (row: string) => row.replace(/^\s*\||\|\s*$/g, '').split('|')
      const head = h(
        'tr',
        cells(line).map((cell) => h('th', inline(cell.trim()))),
      )
      i += 2
      const rows: VNodeChild[] = []
      while (i < lines.length && (lines[i] || '').includes('|'))
        rows.push(
          h(
            'tr',
            cells(lines[i++] || '').map((cell) => h('td', inline(cell.trim()))),
          ),
        )
      nodes.push(h('table', [h('thead', [head]), h('tbody', rows)]))
    } else {
      const paragraph: string[] = [line]
      i++
      while (
        i < lines.length &&
        (lines[i] || '').trim() &&
        !/^(\s*```|#{1,6}\s|>\s?|\s*[-*+]\s|\s*\d+\.\s)/.test(lines[i] || '')
      )
        paragraph.push(lines[i++] || '')
      nodes.push(h('p', inline(paragraph.join('\n'))))
    }
  }
  return nodes
}

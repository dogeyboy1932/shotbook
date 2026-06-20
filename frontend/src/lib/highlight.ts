// Wraps the portion of `range` that falls inside each paragraph element in a
// yellow <mark>, and returns the paragraph_ids (in the order the elements
// were given) whose text the range actually overlaps.
//
// Each paragraph renders as a single plain-text child (one Text node), so
// clipping the selection to a paragraph never needs general boundary-point
// arithmetic across mixed element/text containers (which is easy to get
// wrong -- Range.compareBoundaryPoints compares positions in DOM tree order,
// not character offsets, once the two ranges' containers differ). Instead,
// for a given paragraph's text node we only need to know: did the original
// range's start/end fall inside *this* text node, or did it start/end in a
// different paragraph entirely (in which case this paragraph is covered
// edge-to-edge)?
export const HIGHLIGHT_CLASS = 'selection-highlight'

export function clearHighlights(container: HTMLElement): void {
  const marks = container.querySelectorAll<HTMLElement>(`mark.${HIGHLIGHT_CLASS}`)
  marks.forEach((mark) => {
    const parent = mark.parentNode
    if (!parent) return
    while (mark.firstChild) {
      parent.insertBefore(mark.firstChild, mark)
    }
    parent.removeChild(mark)
    parent.normalize()
  })
}

export function highlightRangeAcrossParagraphs(
  range: Range,
  paragraphs: { id: number; el: HTMLElement }[],
): number[] {
  const matchedIds: number[] = []

  for (const { id, el } of paragraphs) {
    if (!range.intersectsNode(el)) continue

    const textNode = el.firstChild
    if (!textNode || textNode.nodeType !== Node.TEXT_NODE) continue
    const text = textNode as Text

    const startOffset = range.startContainer === text ? range.startOffset : 0
    const endOffset = range.endContainer === text ? range.endOffset : text.length
    if (startOffset >= endOffset) continue

    matchedIds.push(id)

    const clipped = document.createRange()
    clipped.setStart(text, startOffset)
    clipped.setEnd(text, endOffset)

    const mark = document.createElement('mark')
    mark.className = `${HIGHLIGHT_CLASS} bg-yellow-300 text-slate-900 rounded-sm`
    try {
      clipped.surroundContents(mark)
    } catch {
      // Defensive: should be unreachable given the single-text-node
      // guarantee above, but skip rather than corrupt the DOM if it occurs.
    }
  }

  return matchedIds
}

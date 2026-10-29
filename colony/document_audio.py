"""Bounded reading sections for local documents; no synthesis or model work here."""
import re

CLIP = 1000
SECTION = 6000


def _bounded(text, limit):
    """Keep every word, preferring paragraph and sentence breaks before a hard bound."""
    text = text.strip()
    while len(text) > limit:
        choices = [text.rfind(mark, limit // 2, limit) for mark in ('\n\n', '. ', '! ', '? ', '\n', ' ')]
        end = next((p + 1 for p in choices if p >= 0), limit)
        yield text[:end].strip()
        text = text[end:].strip()
    if text:
        yield text


def ingest(text, title, markdown=True):
    """Full document as a small section index and bounded speech blocks, independent of the reader preview cap."""
    from .board import spoken_text
    groups, lines, heading = [], [], title
    fence = None

    def flush():
        nonlocal lines
        content = '\n'.join(lines).strip()
        lines = []
        content = spoken_text(content) if markdown else content
        if content.strip():
            groups.append((heading, content))

    source = text.splitlines()
    i = 0
    while i < len(source):
        line = source[i]
        if markdown:
            fenced = re.match(r'^\s{0,3}(`{3,}|~{3,})', line)
            if fenced:
                mark = fenced[1]
                if fence is None:
                    fence = mark
                elif mark[0] == fence[0] and len(mark) >= len(fence):
                    fence = None
                i += 1
                continue
            if fence:
                i += 1
                continue
            atx = re.match(r'^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$', line)
            setext = i + 1 < len(source) and line.strip() and re.fullmatch(r'\s{0,3}(?:=+|-+)\s*', source[i + 1])
            if atx or setext:
                flush()
                heading = spoken_text(atx[1] if atx else line).strip() or title
                lines.append(heading)
                i += 1 if atx else 2
                continue
        lines.append(line)
        i += 1
    flush()
    blocks, sections = [], []
    for label, content in groups:
        for part, section in enumerate(_bounded(content, SECTION)):
            start = len(blocks)
            for clip in _bounded(section, CLIP):
                blocks.append({'k': f'document-{len(blocks)}', 'text': clip})
            sections.append({'title': label + (f' · continued {part + 1}' if part else ''), 'start': start, 'end': len(blocks)})
    return {'title': title, 'sections': sections, 'blocks': blocks}

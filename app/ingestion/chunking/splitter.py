from typing import List
import logfire

def _split_long(line: str, chunk_size: int) -> List[str]:
    """Hard-splits a single oversized line on whitespace."""
    pieces, current = [], ""
    for word in line.split(" "):
        if current and len(current) + len(word) + 1 > chunk_size:
            pieces.append(current)
            current = word
        else:
            current = f"{current} {word}" if current else word
    if current:
        pieces.append(current)
    # A single "word" longer than chunk_size (e.g. base64) is cut by characters
    return [p[i:i + chunk_size] for p in pieces for i in range(0, len(p), chunk_size)]

def chunk_text(text: str, chunk_size: int = 1500, overlap: int = 200) -> List[str]:
    """
    Line-aware chunker: packs lines into chunks of at most `chunk_size` characters,
    carrying the last ~`overlap` characters of lines into the next chunk for context.
    """
    with logfire.span("✂️ Text Chunking", text_length=len(text)):
        if not text.strip():
            return []

        lines = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            lines.extend(_split_long(line, chunk_size) if len(line) > chunk_size else [line])

        chunks = []
        current: List[str] = []
        current_len = 0

        for line in lines:
            if current and current_len + len(line) + 1 > chunk_size:
                chunks.append("\n".join(current))
                # Keep trailing lines (up to `overlap` chars) as the start of the next chunk
                tail, tail_len = [], 0
                for prev in reversed(current):
                    if tail_len + len(prev) + 1 > overlap:
                        break
                    tail.insert(0, prev)
                    tail_len += len(prev) + 1
                if tail_len + len(line) + 1 > chunk_size:
                    tail, tail_len = [], 0
                current, current_len = tail, tail_len
            current.append(line)
            current_len += len(line) + 1

        if current:
            chunks.append("\n".join(current))

        logfire.info(f"✅ Generated {len(chunks)} chunks")
        return chunks

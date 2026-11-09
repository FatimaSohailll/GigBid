"""Chunking strategies."""


def chunk_words(text: str, size: int, overlap: int) -> list[str]:
    """Fixed word windows with overlap, the chunking that baseline V0 requires."""
    words = text.split()
    if not words:
        return []
    step = size - overlap
    chunks = []
    for start in range(0, len(words), step):
        chunks.append(" ".join(words[start : start + size]))
        if start + size >= len(words):
            break
    return chunks

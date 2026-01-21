"""Document chunking for embedding generation.

This module provides text chunking functionality with configurable
chunk size and overlap. Chunks are designed to be semantically
meaningful units for embedding and retrieval.
"""

import logging
from dataclasses import dataclass
from typing import List

logger = logging.getLogger(__name__)


@dataclass
class Chunk:
    """A chunk of document text with metadata."""
    
    text: str
    index: int
    start_char: int
    end_char: int


class DocumentChunker:
    """Chunks documents with configurable size and overlap.
    
    Splits documents into overlapping chunks suitable for embedding.
    The overlap ensures that context is preserved across chunk boundaries.
    
    Usage:
        chunker = DocumentChunker(chunk_size=1000, chunk_overlap=200)
        chunks = chunker.chunk("Long document text...")
        
        for chunk in chunks:
            print(f"Chunk {chunk.index}: {chunk.text[:50]}...")
    """
    
    def __init__(self, chunk_size: int = 1000, chunk_overlap: int = 200):
        """Initialize the chunker.
        
        Args:
            chunk_size: Maximum size of each chunk in characters
            chunk_overlap: Overlap between consecutive chunks in characters
        """
        if chunk_overlap >= chunk_size:
            raise ValueError("chunk_overlap must be less than chunk_size")
        
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
    
    def chunk(self, content: str) -> List[Chunk]:
        """Split content into overlapping chunks.
        
        Args:
            content: Document text to chunk
            
        Returns:
            List of Chunk objects
        """
        if not content or not content.strip():
            return []
        
        content = content.strip()
        
        if len(content) <= self.chunk_size:
            return [Chunk(
                text=content,
                index=0,
                start_char=0,
                end_char=len(content),
            )]
        
        chunks = []
        start = 0
        index = 0
        step = self.chunk_size - self.chunk_overlap
        
        while start < len(content):
            end = min(start + self.chunk_size, len(content))
            
            chunk_text = content[start:end]
            
            if start > 0:
                chunk_text = self._find_clean_start(chunk_text)
            
            if end < len(content):
                chunk_text = self._find_clean_end(chunk_text)
            
            if chunk_text.strip():
                chunks.append(Chunk(
                    text=chunk_text.strip(),
                    index=index,
                    start_char=start,
                    end_char=end,
                ))
                index += 1
            
            start += step
            
            if start >= len(content):
                break
        
        logger.debug(f"Created {len(chunks)} chunks from {len(content)} characters")
        return chunks
    
    def _find_clean_start(self, text: str) -> str:
        """Find a clean starting point (after a sentence or paragraph break).
        
        Args:
            text: Text to find clean start in
            
        Returns:
            Text starting at a clean boundary
        """
        search_limit = min(len(text) // 4, 200)
        
        for i, char in enumerate(text[:search_limit]):
            if char in ".!?\n" and i + 1 < len(text):
                next_char = text[i + 1]
                if next_char in " \n\t":
                    return text[i + 1:].lstrip()
        
        for i, char in enumerate(text[:search_limit]):
            if char == " ":
                return text[i + 1:]
        
        return text
    
    def _find_clean_end(self, text: str) -> str:
        """Find a clean ending point (at a sentence or paragraph break).
        
        Args:
            text: Text to find clean end in
            
        Returns:
            Text ending at a clean boundary
        """
        search_start = max(0, len(text) - min(len(text) // 4, 200))
        
        for i in range(len(text) - 1, search_start, -1):
            char = text[i]
            if char in ".!?\n":
                return text[:i + 1]
        
        for i in range(len(text) - 1, search_start, -1):
            if text[i] == " ":
                return text[:i]
        
        return text
    
    def chunk_with_metadata(
        self,
        content: str,
        source: str,
    ) -> List[dict]:
        """Chunk content and return with full metadata.
        
        Args:
            content: Document text to chunk
            source: Source file path for metadata
            
        Returns:
            List of dicts with text, source, and chunk_index
        """
        chunks = self.chunk(content)
        
        return [
            {
                "text": chunk.text,
                "source": source,
                "chunk_index": chunk.index,
                "content": chunk.text,
            }
            for chunk in chunks
        ]

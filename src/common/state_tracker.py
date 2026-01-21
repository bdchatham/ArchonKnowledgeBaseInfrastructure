"""PostgreSQL state tracker for document versions.

This module tracks the state of monitored documents to enable efficient
change detection. It stores SHA hashes and timestamps for each document,
allowing the Monitor service to detect which documents have changed.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

import asyncpg

logger = logging.getLogger(__name__)


@dataclass
class DocumentState:
    """State of a tracked document."""
    
    repo_file_path: str
    sha: str
    last_modified: datetime
    last_checked: datetime
    content_hash: Optional[str] = None


class StateTrackerError(Exception):
    """Raised when state tracker operations fail."""
    pass


class StateTracker:
    """PostgreSQL state tracker for document versions.
    
    Tracks document state to enable efficient change detection during
    monitoring. Each document is identified by its repo_file_path
    (e.g., "github.com/user/repo/.kiro/docs/overview.md").
    
    Usage:
        tracker = StateTracker(db_url="postgresql://...")
        await tracker.connect()
        
        # Get current state
        state = await tracker.get_document_state("repo/file.md")
        
        # Update state after processing
        await tracker.update_document_state(DocumentState(...))
        
        await tracker.close()
    """
    
    def __init__(self, db_url: str):
        """Initialize the state tracker.
        
        Args:
            db_url: PostgreSQL connection URL
        """
        self.db_url = db_url
        self._pool: asyncpg.Pool | None = None
    
    async def connect(self) -> None:
        """Connect to the database."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self.db_url)
            logger.info("Connected to state tracker database")
    
    async def close(self) -> None:
        """Close the database connection."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("Closed state tracker database connection")
    
    async def _get_pool(self) -> asyncpg.Pool:
        """Get the connection pool, connecting if necessary."""
        if self._pool is None:
            await self.connect()
        return self._pool
    
    async def get_document_state(
        self,
        repo_file_path: str,
    ) -> Optional[DocumentState]:
        """Get the current state of a document.
        
        Args:
            repo_file_path: Unique path identifying the document
            
        Returns:
            DocumentState if found, None otherwise
        """
        pool = await self._get_pool()
        
        try:
            row = await pool.fetchrow(
                """
                SELECT repo_file_path, sha, last_modified, last_checked, content_hash
                FROM document_state
                WHERE repo_file_path = $1
                """,
                repo_file_path,
            )
            
            if row is None:
                return None
                
            return DocumentState(
                repo_file_path=row["repo_file_path"],
                sha=row["sha"],
                last_modified=row["last_modified"],
                last_checked=row["last_checked"],
                content_hash=row["content_hash"],
            )
            
        except asyncpg.PostgresError as e:
            logger.error(f"Failed to get document state for {repo_file_path}: {e}")
            raise StateTrackerError(f"Failed to get document state: {e}") from e
    
    async def update_document_state(self, state: DocumentState) -> None:
        """Update or insert document state.
        
        Args:
            state: DocumentState to upsert
        """
        pool = await self._get_pool()
        
        try:
            await pool.execute(
                """
                INSERT INTO document_state (repo_file_path, sha, last_modified, last_checked, content_hash)
                VALUES ($1, $2, $3, $4, $5)
                ON CONFLICT (repo_file_path) DO UPDATE SET
                    sha = EXCLUDED.sha,
                    last_modified = EXCLUDED.last_modified,
                    last_checked = EXCLUDED.last_checked,
                    content_hash = EXCLUDED.content_hash
                """,
                state.repo_file_path,
                state.sha,
                state.last_modified,
                state.last_checked,
                state.content_hash,
            )
            logger.debug(f"Updated state for: {state.repo_file_path}")
            
        except asyncpg.PostgresError as e:
            logger.error(f"Failed to update document state: {e}")
            raise StateTrackerError(f"Failed to update document state: {e}") from e
    
    async def delete_document_state(self, repo_file_path: str) -> None:
        """Remove document state (for deleted documents).
        
        Args:
            repo_file_path: Path of the document to remove
        """
        pool = await self._get_pool()
        
        try:
            await pool.execute(
                """
                DELETE FROM document_state
                WHERE repo_file_path = $1
                """,
                repo_file_path,
            )
            logger.debug(f"Deleted state for: {repo_file_path}")
            
        except asyncpg.PostgresError as e:
            logger.error(f"Failed to delete document state: {e}")
            raise StateTrackerError(f"Failed to delete document state: {e}") from e
    
    async def get_all_tracked_paths(self, repo_prefix: str) -> List[str]:
        """Get all tracked paths for a repository.
        
        Args:
            repo_prefix: Repository URL prefix to filter by
            
        Returns:
            List of repo_file_paths matching the prefix
        """
        pool = await self._get_pool()
        
        try:
            rows = await pool.fetch(
                """
                SELECT repo_file_path
                FROM document_state
                WHERE repo_file_path LIKE $1
                """,
                f"{repo_prefix}%",
            )
            
            return [row["repo_file_path"] for row in rows]
            
        except asyncpg.PostgresError as e:
            logger.error(f"Failed to get tracked paths: {e}")
            raise StateTrackerError(f"Failed to get tracked paths: {e}") from e
    
    async def mark_checked(self, repo_file_path: str) -> None:
        """Update the last_checked timestamp for a document.
        
        Args:
            repo_file_path: Path of the document to mark as checked
        """
        pool = await self._get_pool()
        
        try:
            await pool.execute(
                """
                UPDATE document_state
                SET last_checked = $2
                WHERE repo_file_path = $1
                """,
                repo_file_path,
                datetime.utcnow(),
            )
            
        except asyncpg.PostgresError as e:
            logger.error(f"Failed to mark document as checked: {e}")
            raise StateTrackerError(f"Failed to mark checked: {e}") from e
    
    async def health_check(self) -> bool:
        """Check if the database is healthy.
        
        Returns:
            True if database is reachable and table exists
        """
        try:
            pool = await self._get_pool()
            await pool.fetchval("SELECT 1 FROM document_state LIMIT 1")
            return True
        except Exception:
            return False

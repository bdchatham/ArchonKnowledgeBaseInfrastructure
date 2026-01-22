"""Monitor service entry point.

This module is the main entry point for the Monitor CronJob. It processes
configured repositories, detects document changes, and triggers ingestion
for new or modified documents.
"""

import asyncio
import hashlib
import json
import logging
from datetime import datetime
from typing import List

from ..common.config import Settings
from aphex_clients import EmbeddingClient
from ..common.vector_store import VectorStore
from ..common.state_tracker import StateTracker, DocumentState
from .github_client import GitHubClient, GitHubFile
from .chunker import DocumentChunker
from .ingester import DocumentIngester

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def process_repository(
    repo_config: dict,
    github_client: GitHubClient,
    state_tracker: StateTracker,
    ingester: DocumentIngester,
) -> tuple[int, int, int]:
    """Process a single repository for changes.
    
    Args:
        repo_config: Repository configuration dict with url, branch, paths
        github_client: GitHub API client
        state_tracker: Document state tracker
        ingester: Document ingester
        
    Returns:
        Tuple of (new_count, updated_count, deleted_count)
    """
    repo_url = repo_config["url"]
    branch = repo_config.get("branch", "main")
    paths = repo_config.get("paths", [".kiro/docs"])
    
    owner, repo = GitHubClient.parse_repo_url(repo_url)
    logger.info(f"Processing repository: {owner}/{repo} (branch: {branch})")
    
    current_files: List[GitHubFile] = []
    for path in paths:
        files = await github_client.list_files(owner, repo, path, branch)
        current_files.extend(files)
    
    logger.info(f"Found {len(current_files)} files in {owner}/{repo}")
    
    current_paths = set()
    new_count = 0
    updated_count = 0
    
    for file in current_files:
        repo_file_path = f"{repo_url}/{file.path}"
        current_paths.add(repo_file_path)
        
        state = await state_tracker.get_document_state(repo_file_path)
        
        if state is None:
            logger.info(f"New document: {repo_file_path}")
            content = await github_client.get_file_content(owner, repo, file.path, branch)
            await ingester.ingest_document(repo_file_path, content)
            
            await state_tracker.update_document_state(DocumentState(
                repo_file_path=repo_file_path,
                sha=file.sha,
                last_modified=datetime.utcnow(),
                last_checked=datetime.utcnow(),
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
            ))
            new_count += 1
            
        elif state.sha != file.sha:
            logger.info(f"Updated document: {repo_file_path}")
            content = await github_client.get_file_content(owner, repo, file.path, branch)
            await ingester.ingest_document(repo_file_path, content)
            
            await state_tracker.update_document_state(DocumentState(
                repo_file_path=repo_file_path,
                sha=file.sha,
                last_modified=datetime.utcnow(),
                last_checked=datetime.utcnow(),
                content_hash=hashlib.sha256(content.encode()).hexdigest(),
            ))
            updated_count += 1
            
        else:
            await state_tracker.mark_checked(repo_file_path)
    
    tracked_paths = await state_tracker.get_all_tracked_paths(repo_url)
    deleted_count = 0
    
    for tracked_path in tracked_paths:
        if tracked_path not in current_paths:
            logger.info(f"Deleted document: {tracked_path}")
            await ingester.remove_document(tracked_path)
            await state_tracker.delete_document_state(tracked_path)
            deleted_count += 1
    
    return new_count, updated_count, deleted_count


async def main():
    """Monitor entry point - runs once per CronJob execution."""
    logger.info("Starting monitor run")
    
    settings = Settings()
    
    repositories = json.loads(settings.repositories)
    if not repositories:
        logger.warning("No repositories configured, exiting")
        return
    
    logger.info(f"Processing {len(repositories)} repositories")
    
    github_client = GitHubClient(token=settings.github_token)
    state_tracker = StateTracker(db_url=settings.tracker_db_url)
    embedding_client = EmbeddingClient(
        base_url=settings.embedding_service_url,
        model=settings.embedding_model,
    )
    vector_store = VectorStore(
        url=settings.vector_db_url,
        collection=settings.collection_name,
    )
    chunker = DocumentChunker(
        chunk_size=settings.chunk_size,
        chunk_overlap=settings.chunk_overlap,
    )
    ingester = DocumentIngester(
        embedding_client=embedding_client,
        vector_store=vector_store,
        chunker=chunker,
    )
    
    try:
        await state_tracker.connect()
        
        total_new = 0
        total_updated = 0
        total_deleted = 0
        
        for repo_config in repositories:
            try:
                new, updated, deleted = await process_repository(
                    repo_config,
                    github_client,
                    state_tracker,
                    ingester,
                )
                total_new += new
                total_updated += updated
                total_deleted += deleted
                
            except Exception as e:
                logger.error(f"Failed to process repository {repo_config.get('url')}: {e}")
                continue
        
        logger.info(
            f"Monitor run complete: {total_new} new, "
            f"{total_updated} updated, {total_deleted} deleted"
        )
        
    finally:
        await state_tracker.close()
        await github_client.close()
        await embedding_client.close()


if __name__ == "__main__":
    asyncio.run(main())

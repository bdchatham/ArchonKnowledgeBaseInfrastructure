"""GitHub API client for fetching repository contents.

This module provides a client for interacting with the GitHub API to
fetch file listings, content, and SHA hashes for change detection.
"""

import base64
import logging
from dataclasses import dataclass
from typing import List, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class GitHubFile:
    """Represents a file in a GitHub repository."""
    
    path: str
    sha: str
    size: int
    download_url: Optional[str] = None


class GitHubClientError(Exception):
    """Raised when GitHub API operations fail."""
    pass


class GitHubClient:
    """Client for fetching repository contents from GitHub.
    
    Provides methods for listing files in a directory, fetching file
    content, and retrieving SHA hashes for change detection.
    
    Usage:
        client = GitHubClient(token="ghp_...")
        
        # List files in a directory
        files = await client.list_files(
            owner="bdchatham",
            repo="ArchonAgent",
            path=".kiro/docs",
            branch="main"
        )
        
        # Get file content
        content = await client.get_file_content(
            owner="bdchatham",
            repo="ArchonAgent",
            path=".kiro/docs/overview.md",
            branch="main"
        )
    """
    
    BASE_URL = "https://api.github.com"
    
    def __init__(self, token: Optional[str] = None, timeout: float = 30.0):
        """Initialize the GitHub client.
        
        Args:
            token: GitHub personal access token (optional but recommended)
            timeout: Request timeout in seconds
        """
        self.token = token
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
    
    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            headers = {
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "ArchonKnowledgeBase/1.0",
            }
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            
            self._client = httpx.AsyncClient(
                headers=headers,
                timeout=self.timeout,
            )
        return self._client
    
    async def close(self):
        """Close the HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
    
    async def list_files(
        self,
        owner: str,
        repo: str,
        path: str,
        branch: str = "main",
    ) -> List[GitHubFile]:
        """List files in a repository directory.
        
        Args:
            owner: Repository owner (user or organization)
            repo: Repository name
            path: Path within the repository
            branch: Branch name (default: main)
            
        Returns:
            List of GitHubFile objects for files in the directory
        """
        client = await self._get_client()
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/contents/{path}"
        
        try:
            response = await client.get(url, params={"ref": branch})
            
            if response.status_code == 404:
                logger.warning(f"Path not found: {owner}/{repo}/{path}")
                return []
            
            response.raise_for_status()
            data = response.json()
            
            if not isinstance(data, list):
                data = [data]
            
            files = []
            for item in data:
                if item["type"] == "file" and item["name"].endswith(".md"):
                    files.append(GitHubFile(
                        path=item["path"],
                        sha=item["sha"],
                        size=item["size"],
                        download_url=item.get("download_url"),
                    ))
                elif item["type"] == "dir":
                    subfiles = await self.list_files(owner, repo, item["path"], branch)
                    files.extend(subfiles)
            
            return files
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                logger.error("GitHub API rate limit exceeded")
                raise GitHubClientError("GitHub API rate limit exceeded") from e
            logger.error(f"GitHub API error: {e.response.status_code}")
            raise GitHubClientError(f"GitHub API error: {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error(f"GitHub request failed: {e}")
            raise GitHubClientError(f"GitHub request failed: {e}") from e
    
    async def get_file_content(
        self,
        owner: str,
        repo: str,
        path: str,
        branch: str = "main",
    ) -> str:
        """Get the content of a file.
        
        Args:
            owner: Repository owner
            repo: Repository name
            path: File path within the repository
            branch: Branch name
            
        Returns:
            File content as a string
        """
        client = await self._get_client()
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/contents/{path}"
        
        try:
            response = await client.get(url, params={"ref": branch})
            response.raise_for_status()
            
            data = response.json()
            
            if data.get("encoding") == "base64":
                content = base64.b64decode(data["content"]).decode("utf-8")
            else:
                download_url = data.get("download_url")
                if download_url:
                    content_response = await client.get(download_url)
                    content_response.raise_for_status()
                    content = content_response.text
                else:
                    raise GitHubClientError(f"Cannot fetch content for {path}")
            
            return content
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to fetch file content: {e.response.status_code}")
            raise GitHubClientError(f"Failed to fetch {path}: {e.response.status_code}") from e
        except httpx.RequestError as e:
            logger.error(f"GitHub request failed: {e}")
            raise GitHubClientError(f"GitHub request failed: {e}") from e
    
    async def get_file_sha(
        self,
        owner: str,
        repo: str,
        path: str,
        branch: str = "main",
    ) -> Optional[str]:
        """Get the SHA hash of a file.
        
        Args:
            owner: Repository owner
            repo: Repository name
            path: File path within the repository
            branch: Branch name
            
        Returns:
            SHA hash string, or None if file not found
        """
        client = await self._get_client()
        url = f"{self.BASE_URL}/repos/{owner}/{repo}/contents/{path}"
        
        try:
            response = await client.get(url, params={"ref": branch})
            
            if response.status_code == 404:
                return None
            
            response.raise_for_status()
            data = response.json()
            return data.get("sha")
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to get file SHA: {e.response.status_code}")
            raise GitHubClientError(f"Failed to get SHA for {path}") from e
        except httpx.RequestError as e:
            logger.error(f"GitHub request failed: {e}")
            raise GitHubClientError(f"GitHub request failed: {e}") from e
    
    @staticmethod
    def parse_repo_url(url: str) -> tuple[str, str]:
        """Parse a GitHub URL into owner and repo.
        
        Args:
            url: GitHub repository URL (e.g., https://github.com/owner/repo)
            
        Returns:
            Tuple of (owner, repo)
        """
        url = url.rstrip("/")
        if url.endswith(".git"):
            url = url[:-4]
        
        parts = url.split("/")
        if len(parts) >= 2:
            return parts[-2], parts[-1]
        
        raise ValueError(f"Invalid GitHub URL: {url}")

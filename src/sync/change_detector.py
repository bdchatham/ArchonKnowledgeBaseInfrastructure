"""Change detection service for hash-based synchronization.

This module provides the ChangeDetector class for detecting changes in SCIP
indexes using hash comparison. It enables efficient synchronization by skipping
packages whose SCIP index has not changed since the last sync.

The change detector stores hash state in a JSON file (default: `.archon/sync-state.json`)
and provides methods for comparing, loading, saving, and updating hash state.

State File Schema:
    {
      "packages": {
        "ArchonDocumentationMCPTools": {
          "index_hash": "abc123...",
          "last_synced": "2024-01-15T10:30:00Z"
        }
      }
    }

Source:
- .kiro/specs/sync-service/design.md (Change Detector section)
- .kiro/specs/sync-service/requirements.md (Requirements 7.1, 7.2)
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.sync.models import HashState


class ChangeDetector:
    """Service for hash-based change detection.

    Detects changes in SCIP indexes by comparing the current hash with the
    stored hash from the last successful sync. This enables efficient
    synchronization by skipping packages that haven't changed.

    The hash state is persisted to a JSON file, allowing change detection
    to work across process restarts.

    Attributes:
        state_file: Path to the JSON file storing hash state

    Example:
        >>> detector = ChangeDetector(state_file=".archon/sync-state.json")
        >>> if detector.has_changed("MyPackage", "abc123..."):
        ...     # Perform sync
        ...     detector.update_hash("MyPackage", "abc123...")

    Validates:
        Requirement 7.1 (Skip synchronization when hash unchanged)
        Requirement 7.2 (Store last synchronized index hash per package)
    """

    def __init__(self, state_file: str = ".archon/sync-state.json") -> None:
        """Initialize the change detector.

        Args:
            state_file: Path to the state file for storing hashes.
                       Defaults to `.archon/sync-state.json`.
        """
        self.state_file = Path(state_file)

    def has_changed(self, package: str, current_hash: str) -> bool:
        """Check if a package's SCIP index has changed.

        Compares the current hash with the stored hash for the package.
        Returns True if the hash differs or if no stored hash exists.

        Args:
            package: Package name
            current_hash: Hash of the current SCIP index

        Returns:
            True if hash differs from stored hash or no stored hash exists,
            False if hash matches stored hash
        """
        stored_hash = self.get_stored_hash(package)
        if stored_hash is None:
            return True
        return stored_hash != current_hash

    def get_stored_hash(self, package: str) -> Optional[str]:
        """Get the stored hash for a package.

        Args:
            package: Package name

        Returns:
            Stored hash or None if not found
        """
        state = self.load_state()
        if package in state:
            return state[package].index_hash
        return None

    def update_hash(self, package: str, new_hash: str) -> None:
        """Update the stored hash for a package.

        Updates the stored hash and last_synced timestamp for the package,
        then persists the state to the state file.

        Args:
            package: Package name
            new_hash: New hash to store
        """
        state = self.load_state()
        timestamp = datetime.now(timezone.utc).isoformat()
        state[package] = HashState(
            package=package,
            index_hash=new_hash,
            last_synced=timestamp,
        )
        self.save_state(state)

    def load_state(self) -> dict[str, HashState]:
        """Load hash state from the state file.

        Reads the state file and deserializes the JSON content into
        HashState objects. Handles missing state file gracefully by
        returning an empty state dictionary.

        Returns:
            Dictionary of package name to HashState
        """
        if not self.state_file.exists():
            return {}

        try:
            content = self.state_file.read_text(encoding="utf-8")
            data = json.loads(content)
        except (json.JSONDecodeError, OSError):
            return {}

        packages_data = data.get("packages", {})
        state: dict[str, HashState] = {}

        for package_name, package_state in packages_data.items():
            state[package_name] = HashState(
                package=package_name,
                index_hash=package_state.get("index_hash", ""),
                last_synced=package_state.get("last_synced", ""),
            )

        return state

    def save_state(self, state: dict[str, HashState]) -> None:
        """Save hash state to the state file.

        Serializes the state dictionary to JSON and writes it to the
        state file. Creates parent directories if they don't exist.

        Args:
            state: Dictionary of package name to HashState
        """
        self.state_file.parent.mkdir(parents=True, exist_ok=True)

        packages_data: dict[str, dict[str, str]] = {}
        for package_name, hash_state in state.items():
            packages_data[package_name] = {
                "index_hash": hash_state.index_hash,
                "last_synced": hash_state.last_synced,
            }

        data = {"packages": packages_data}
        content = json.dumps(data, indent=2)
        self.state_file.write_text(content, encoding="utf-8")

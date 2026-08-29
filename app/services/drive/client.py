"""
Google Drive client — list nested files under a shared root folder.

Auth: service account JSON (folder must be shared with the SA email).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

from app.utils.helpers import get_logger

logger = get_logger(__name__)

FOLDER_MIME = "application/vnd.google-apps.folder"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]


@dataclass(frozen=True)
class DriveItem:
    file_id: str
    name: str
    mime_type: str
    folder_path: str
    is_folder: bool
    modified_time: str | None = None
    size: int | None = None
    md5_checksum: str | None = None

    @property
    def full_path(self) -> str:
        if self.folder_path:
            return f"{self.folder_path}/{self.name}"
        return self.name


class DriveClient:
    def __init__(self, service_account_file: str | Path, root_folder_id: str) -> None:
        path = Path(service_account_file)
        if not path.is_file():
            raise FileNotFoundError(f"Service account JSON not found: {path}")
        if not root_folder_id.strip():
            raise ValueError("DRIVE_ROOT_FOLDER_ID is empty")

        self.root_folder_id = root_folder_id.strip()
        credentials = service_account.Credentials.from_service_account_file(
            str(path),
            scopes=SCOPES,
        )
        self._service = build("drive", "v3", credentials=credentials, cache_discovery=False)

    def get_folder_name(self, folder_id: str | None = None) -> str:
        target = folder_id or self.root_folder_id
        meta = (
            self._service.files()
            .get(fileId=target, fields="id,name", supportsAllDrives=True)
            .execute()
        )
        return str(meta.get("name") or target)

    def list_children(self, folder_id: str) -> list[dict[str, Any]]:
        """List direct children of one folder (handles pagination)."""
        items: list[dict[str, Any]] = []
        page_token: str | None = None
        query = f"'{folder_id}' in parents and trashed = false"

        while True:
            response = (
                self._service.files()
                .list(
                    q=query,
                    spaces="drive",
                    fields=(
                        "nextPageToken, files(id, name, mimeType, modifiedTime, size, md5Checksum)"
                    ),
                    pageToken=page_token,
                    pageSize=1000,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            items.extend(response.get("files") or [])
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        return items

    def list_tree(self, folder_id: str | None = None) -> list[DriveItem]:
        """Recursively list folders and files under the root (or given folder)."""
        start_id = folder_id or self.root_folder_id
        root_name = self.get_folder_name(start_id)
        results: list[DriveItem] = []
        # Queue of (folder_id, folder_path_relative_to_root)
        stack: list[tuple[str, str]] = [(start_id, "")]

        while stack:
            current_id, current_path = stack.pop()
            children = self.list_children(current_id)
            children.sort(key=lambda f: (f.get("mimeType") != FOLDER_MIME, f.get("name") or ""))

            for raw in children:
                name = str(raw.get("name") or "")
                mime = str(raw.get("mimeType") or "")
                file_id = str(raw.get("id") or "")
                is_folder = mime == FOLDER_MIME
                child_path = f"{current_path}/{name}" if current_path else name

                size_raw = raw.get("size")
                size = int(size_raw) if size_raw is not None else None

                item = DriveItem(
                    file_id=file_id,
                    name=name,
                    mime_type=mime,
                    folder_path=current_path,
                    is_folder=is_folder,
                    modified_time=raw.get("modifiedTime"),
                    size=size,
                    md5_checksum=raw.get("md5Checksum"),
                )
                results.append(item)

                if is_folder:
                    stack.append((file_id, child_path))

        logger.info(
            "Listed %d Drive items under %s (%s)",
            len(results),
            root_name,
            start_id,
        )
        return results

    def list_files(self, folder_id: str | None = None) -> list[DriveItem]:
        """Recursively list files only (skip folder rows)."""
        return [item for item in self.list_tree(folder_id) if not item.is_folder]

    def download_file(self, file_id: str, dest: Path) -> Path:
        """Download a binary Drive file to dest (creates parent dirs)."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        request = self._service.files().get_media(
            fileId=file_id,
            supportsAllDrives=True,
        )
        with dest.open("wb") as handle:
            downloader = MediaIoBaseDownload(handle, request)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        logger.info("Downloaded Drive file %s -> %s", file_id, dest)
        return dest

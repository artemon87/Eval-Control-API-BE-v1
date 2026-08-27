from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote


@dataclass(frozen=True)
class GitHubLinkSettings:
    server_url: str = "https://github.com"
    repository: str | None = None
    manifest_repository: str | None = None
    manifest_ref: str = "main"


def enrich_github_links(document: dict, settings: GitHubLinkSettings) -> dict:
    """Return API-only absolute URLs without mutating the stored run document."""
    result = dict(document)
    server = settings.server_url.rstrip("/")
    repository = document.get("github_repository") or settings.repository
    run_id = document.get("github_run_id")
    job_id = document.get("github_job_id")

    if repository and run_id:
        result["github_run_url"] = f"{server}/{repository}/actions/runs/{run_id}"
    if repository and job_id:
        result["github_job_url"] = f"{server}/{repository}/actions/runs/{run_id}/job/{job_id}"

    manifest_path = document.get("manifest_path")
    manifest_repository = document.get("manifest_repository") or settings.manifest_repository
    manifest_ref = document.get("manifest_ref") or settings.manifest_ref
    if manifest_repository and manifest_path:
        encoded_path = "/".join(quote(part, safe="") for part in manifest_path.split("/"))
        result["manifest_url"] = (
            f"{server}/{manifest_repository}/blob/{quote(str(manifest_ref), safe='')}/{encoded_path}"
        )

    return result

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv
from requests import Session
from requests.exceptions import RequestException, Timeout


LOGGER = logging.getLogger("leetcode_downloader")

GRAPHQL_URL = "https://leetcode.com/graphql"
DEFAULT_OUTPUT_DIR = Path("leetcode_submissions")
MANIFEST_NAME = ".submission_index.json"
SUBMISSION_PAGE_SIZE = 20
ENV_FILE_NAME = ".env"

SUBMISSION_LIST_QUERY = """
query submissionList($offset: Int!, $limit: Int!) {
  submissionList(offset: $offset, limit: $limit) {
    hasNext
    submissions {
      id
      status_display: statusDisplay
    }
  }
}
"""

SUBMISSION_LIST_QUERY_WITH_LAST_KEY = """
query submissionList($offset: Int!, $limit: Int!, $lastKey: String) {
  submissionList(offset: $offset, limit: $limit, lastKey: $lastKey) {
    hasNext
    lastKey
    submissions {
      id
      status_display: statusDisplay
    }
  }
}
"""

SUBMISSION_DETAILS_QUERY = """
query submissionDetails($submissionId: Int!) {
  submissionDetails(submissionId: $submissionId) {
    code
    lang
    question {
      titleSlug
    }
  }
}
"""

SUBMISSION_DETAILS_QUERY_WITH_DIRECT_SLUG = """
query submissionDetails($submissionId: Int!) {
  submissionDetails(submissionId: $submissionId) {
    code
    lang
    titleSlug
  }
}
"""

SUBMISSION_DETAILS_QUERY_WITH_LANG_OBJECT = """
query submissionDetails($submissionId: Int!) {
  submissionDetails(submissionId: $submissionId) {
    code
    lang {
      name
      verboseName
    }
    question {
      titleSlug
    }
  }
}
"""

LANGUAGE_EXTENSIONS = {
    "bash": ".sh",
    "c": ".c",
    "c#": ".cs",
    "c++": ".cpp",
    "cpp": ".cpp",
    "csharp": ".cs",
    "dart": ".dart",
    "elixir": ".exs",
    "erlang": ".erl",
    "golang": ".go",
    "go": ".go",
    "java": ".java",
    "javascript": ".js",
    "js": ".js",
    "kotlin": ".kt",
    "mssql": ".sql",
    "mysql": ".sql",
    "oracle": ".sql",
    "oraclesql": ".sql",
    "pandas": ".py",
    "php": ".php",
    "pypy3": ".py",
    "python": ".py",
    "python3": ".py",
    "pythondata": ".py",
    "racket": ".rkt",
    "ruby": ".rb",
    "rust": ".rs",
    "scala": ".scala",
    "shell": ".sh",
    "swift": ".swift",
    "ts": ".ts",
    "typescript": ".ts",
}

SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")


class LeetCodeDownloaderError(RuntimeError):
    """Raised when the downloader cannot complete a request or write a file."""


@dataclass(frozen=True)
class SubmissionDetails:
    submission_id: int
    title_slug: str
    lang: str
    code: str


class LeetCodeDownloader:
    def __init__(
        self,
        leetcode_session: str,
        csrftoken: str,
        output_dir: Path | str = DEFAULT_OUTPUT_DIR,
        request_delay: float = 2.0,
        timeout: float = 30.0,
        session: Session | None = None,
    ) -> None:
        if not leetcode_session:
            raise ValueError("LEETCODE_SESSION is required.")
        if not csrftoken:
            raise ValueError("csrftoken is required.")

        self.output_dir = Path(output_dir)
        self.manifest_path = self.output_dir / MANIFEST_NAME
        self.request_delay = max(request_delay, 0.0)
        self.timeout = timeout
        self.session = session or requests.Session()

        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "Origin": "https://leetcode.com",
                "Referer": "https://leetcode.com/",
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/124.0.0.0 Safari/537.36"
                ),
                "x-csrftoken": csrftoken,
            }
        )
        self.session.cookies.set(
            "LEETCODE_SESSION",
            leetcode_session,
            domain=".leetcode.com",
            path="/",
        )
        self.session.cookies.set(
            "csrftoken",
            csrftoken,
            domain=".leetcode.com",
            path="/",
        )

        self.manifest = self._load_manifest()

    def run(self) -> dict[str, int]:
        self.output_dir.mkdir(parents=True, exist_ok=True)

        accepted_submission_ids = self.fetch_accepted_submission_ids()
        saved_count = 0
        skipped_count = 0
        failed_count = 0

        for index, submission_id in enumerate(accepted_submission_ids, start=1):
            existing_path = self._lookup_existing_backup(submission_id)
            if existing_path is not None:
                skipped_count += 1
                LOGGER.info(
                    "Skipping submission %s (%s/%s), already archived at %s",
                    submission_id,
                    index,
                    len(accepted_submission_ids),
                    existing_path,
                )
                continue

            try:
                details = self.fetch_submission_details(submission_id)
                saved_path = self.save_submission(details)
            except LeetCodeDownloaderError as exc:
                failed_count += 1
                LOGGER.error(
                    "Failed to back up submission %s (%s/%s): %s",
                    submission_id,
                    index,
                    len(accepted_submission_ids),
                    exc,
                )
                continue

            saved_count += 1
            LOGGER.info(
                "Saved submission %s (%s/%s) to %s",
                submission_id,
                index,
                len(accepted_submission_ids),
                saved_path,
            )

        LOGGER.info(
            "Backup completed. saved=%s skipped=%s failed=%s",
            saved_count,
            skipped_count,
            failed_count,
        )
        return {
            "saved": saved_count,
            "skipped": skipped_count,
            "failed": failed_count,
            "total": len(accepted_submission_ids),
        }

    def fetch_accepted_submission_ids(self) -> list[int]:
        accepted_ids: list[int] = []
        offset = 0
        has_next = True

        while has_next:
            data = self._execute_query_variants(
                operation_name="submissionList",
                variants=[
                    (
                        SUBMISSION_LIST_QUERY,
                        {
                            "offset": offset,
                            "limit": SUBMISSION_PAGE_SIZE,
                        },
                    ),
                    (
                        SUBMISSION_LIST_QUERY_WITH_LAST_KEY,
                        {
                            "offset": offset,
                            "limit": SUBMISSION_PAGE_SIZE,
                            "lastKey": None,
                        },
                    ),
                ],
            )

            if "submissionList" not in data or data.get("submissionList") is None:
                raise LeetCodeDownloaderError(
                    "submissionList returned no data. Check your cookies and try again."
                )

            submission_list = data.get("submissionList") or {}
            submissions = submission_list.get("submissions") or []

            if not submissions:
                LOGGER.info("No submissions returned at offset %s. Stopping pagination.", offset)
                break

            page_accepted = 0
            for submission in submissions:
                status_display = submission.get("status_display") or submission.get(
                    "statusDisplay"
                )
                if status_display != "Accepted":
                    continue

                submission_id = submission.get("id")
                if submission_id is None:
                    LOGGER.warning("Skipping accepted submission with no id at offset %s.", offset)
                    continue

                try:
                    accepted_ids.append(int(submission_id))
                except (TypeError, ValueError):
                    LOGGER.warning(
                        "Skipping submission with non-numeric id %r at offset %s.",
                        submission_id,
                        offset,
                    )
                    continue
                page_accepted += 1

            LOGGER.info(
                "Fetched offset=%s count=%s accepted=%s",
                offset,
                len(submissions),
                page_accepted,
            )

            has_next = bool(submission_list.get("hasNext"))
            offset += SUBMISSION_PAGE_SIZE

        LOGGER.info("Collected %s accepted submissions.", len(accepted_ids))
        return accepted_ids

    def fetch_submission_details(self, submission_id: int) -> SubmissionDetails:
        data = self._execute_query_variants(
            operation_name="submissionDetails",
            variants=[
                (
                    SUBMISSION_DETAILS_QUERY,
                    {"submissionId": submission_id},
                ),
                (
                    SUBMISSION_DETAILS_QUERY_WITH_DIRECT_SLUG,
                    {"submissionId": submission_id},
                ),
                (
                    SUBMISSION_DETAILS_QUERY_WITH_LANG_OBJECT,
                    {"submissionId": submission_id},
                ),
            ],
        )

        if "submissionDetails" not in data or data.get("submissionDetails") is None:
            raise LeetCodeDownloaderError(
                f"submissionDetails returned no data for submission {submission_id}."
            )

        raw_details = data.get("submissionDetails") or {}
        code = raw_details.get("code")
        lang = self._normalize_lang(raw_details.get("lang"))
        title_slug = self._extract_title_slug(raw_details)

        if code is None:
            raise LeetCodeDownloaderError(
                f"submissionDetails for {submission_id} did not include code."
            )
        if not lang:
            raise LeetCodeDownloaderError(
                f"submissionDetails for {submission_id} did not include lang."
            )
        if not title_slug:
            raise LeetCodeDownloaderError(
                f"submissionDetails for {submission_id} did not include titleSlug."
            )

        return SubmissionDetails(
            submission_id=submission_id,
            title_slug=title_slug,
            lang=lang,
            code=code,
        )

    def save_submission(self, details: SubmissionDetails) -> Path:
        target_path = self._build_output_path(details.title_slug, details.lang)
        try:
            target_path.write_text(details.code, encoding="utf-8")
        except OSError as exc:
            raise LeetCodeDownloaderError(f"Could not write {target_path}: {exc}") from exc

        self.manifest[str(details.submission_id)] = target_path.name
        self._write_manifest()
        return target_path

    def _execute_query_variants(
        self,
        operation_name: str,
        variants: list[tuple[str, dict[str, Any]]],
    ) -> dict[str, Any]:
        failure_reasons: list[str] = []

        for query, variables in variants:
            try:
                payload = self._post_graphql(
                    operation_name=operation_name,
                    query=query,
                    variables=variables,
                )
            except LeetCodeDownloaderError as exc:
                failure_reasons.append(str(exc))
                continue

            graphql_errors = payload.get("errors") or []
            if graphql_errors:
                failure_reasons.append(self._format_graphql_errors(graphql_errors))
                LOGGER.debug(
                    "GraphQL returned errors for %s with variables %s: %s",
                    operation_name,
                    variables,
                    graphql_errors,
                )
                continue

            data = payload.get("data")
            if isinstance(data, dict):
                return data

            failure_reasons.append(
                f"{operation_name} response was missing a valid 'data' object."
            )

        raise LeetCodeDownloaderError(
            f"All query variants failed for {operation_name}: "
            + " | ".join(failure_reasons)
        )

    def _post_graphql(
        self,
        operation_name: str,
        query: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "operationName": operation_name,
            "query": query,
            "variables": variables,
        }

        try:
            try:
                response = self.session.post(
                    GRAPHQL_URL,
                    json=payload,
                    timeout=self.timeout,
                )
                response.raise_for_status()
            except Timeout as exc:
                raise LeetCodeDownloaderError(
                    f"{operation_name} timed out after {self.timeout} seconds."
                ) from exc
            except RequestException as exc:
                raise LeetCodeDownloaderError(f"{operation_name} request failed: {exc}") from exc

            try:
                raw_payload = response.json()
            except (json.JSONDecodeError, ValueError) as exc:
                snippet = response.text[:200].replace("\n", " ").strip()
                raise LeetCodeDownloaderError(
                    f"{operation_name} returned invalid JSON: {snippet}"
                ) from exc

            if not isinstance(raw_payload, dict):
                raise LeetCodeDownloaderError(
                    f"{operation_name} returned an unexpected JSON payload."
                )

            return raw_payload
        finally:
            time.sleep(self.request_delay)

    def _load_manifest(self) -> dict[str, str]:
        if not self.manifest_path.exists():
            return {}

        try:
            raw_manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.warning(
                "Manifest %s could not be loaded and will be rebuilt: %s",
                self.manifest_path,
                exc,
            )
            return {}

        if not isinstance(raw_manifest, dict):
            LOGGER.warning("Manifest %s is not a JSON object. Ignoring it.", self.manifest_path)
            return {}

        cleaned_manifest: dict[str, str] = {}
        for submission_id, filename in raw_manifest.items():
            if not isinstance(filename, str):
                continue
            cleaned_manifest[str(submission_id)] = filename
        return cleaned_manifest

    def _write_manifest(self) -> None:
        try:
            self.manifest_path.write_text(
                json.dumps(self.manifest, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except OSError as exc:
            LOGGER.warning("Could not update manifest %s: %s", self.manifest_path, exc)

    def _lookup_existing_backup(self, submission_id: int) -> Path | None:
        filename = self.manifest.get(str(submission_id))
        if not filename:
            return None

        backup_path = self.output_dir / filename
        if backup_path.exists():
            return backup_path

        LOGGER.warning(
            "Manifest entry for submission %s points to missing file %s. Re-downloading.",
            submission_id,
            backup_path,
        )
        self.manifest.pop(str(submission_id), None)
        return None

    def _build_output_path(self, title_slug: str, lang: str) -> Path:
        safe_slug = self._sanitize_filename(title_slug)
        extension = self._extension_for_lang(lang)

        candidate = self.output_dir / f"{safe_slug}{extension}"
        if not candidate.exists():
            return candidate

        index = 1
        while True:
            candidate = self.output_dir / f"{safe_slug}_{index}{extension}"
            if not candidate.exists():
                return candidate
            index += 1

    @staticmethod
    def _sanitize_filename(title_slug: str) -> str:
        cleaned = SAFE_FILENAME_PATTERN.sub("_", title_slug.strip())
        cleaned = cleaned.strip("._")
        return cleaned or "submission"

    @staticmethod
    def _extract_title_slug(raw_details: dict[str, Any]) -> str:
        direct_slug = raw_details.get("titleSlug") or raw_details.get("title_slug")
        if isinstance(direct_slug, str) and direct_slug.strip():
            return direct_slug.strip()

        question = raw_details.get("question")
        if isinstance(question, dict):
            nested_slug = question.get("titleSlug") or question.get("title_slug")
            if isinstance(nested_slug, str) and nested_slug.strip():
                return nested_slug.strip()

        return ""

    @staticmethod
    def _normalize_lang(raw_lang: Any) -> str:
        if isinstance(raw_lang, str):
            return raw_lang.strip()

        if isinstance(raw_lang, dict):
            for key in ("name", "slug", "verboseName", "value"):
                value = raw_lang.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()

        return ""

    @staticmethod
    def _extension_for_lang(lang: str) -> str:
        normalized = lang.strip().lower()
        candidates = (
            normalized,
            normalized.replace(" ", ""),
            normalized.replace(" ", "-"),
            normalized.replace(" ", "_"),
        )

        for candidate in candidates:
            extension = LANGUAGE_EXTENSIONS.get(candidate)
            if extension:
                return extension

        fallback = re.sub(r"[^a-z0-9]+", "", normalized)
        if fallback:
            LOGGER.warning("Unknown language %r. Falling back to .%s", lang, fallback)
            return f".{fallback}"

        LOGGER.warning("Unknown language %r. Falling back to .txt", lang)
        return ".txt"

    @staticmethod
    def _format_graphql_errors(errors: list[Any]) -> str:
        messages: list[str] = []
        for error in errors:
            if isinstance(error, dict):
                message = error.get("message")
                if isinstance(message, str):
                    messages.append(message)
                    continue
            messages.append(str(error))
        return "; ".join(messages) or "Unknown GraphQL error."


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Back up all accepted LeetCode submissions to the local filesystem."
    )
    parser.add_argument(
        "--leetcode-session",
        default=os.getenv("LEETCODE_SESSION"),
        help="LeetCode LEETCODE_SESSION cookie value. Defaults to the LEETCODE_SESSION environment variable.",
    )
    parser.add_argument(
        "--csrftoken",
        default=os.getenv("csrftoken") or os.getenv("CSRFTOKEN"),
        help="LeetCode csrftoken cookie value. Defaults to the csrftoken environment variable.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="Directory where accepted submissions will be saved.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="Per-request timeout in seconds.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR"),
        help="Logging verbosity.",
    )
    return parser.parse_args()


def configure_logging(log_level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, log_level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def main() -> int:
    env_path = Path(__file__).resolve().with_name(ENV_FILE_NAME)
    env_loaded = load_dotenv(dotenv_path=env_path, override=False)
    args = parse_args()
    configure_logging(args.log_level)

    if env_loaded:
        LOGGER.debug("Loaded environment variables from %s", env_path)

    if not args.leetcode_session:
        raise SystemExit(
            "Missing LEETCODE_SESSION. Add it to .env, pass --leetcode-session, or export LEETCODE_SESSION."
        )
    if not args.csrftoken:
        raise SystemExit("Missing csrftoken. Add it to .env, pass --csrftoken, or export csrftoken.")

    downloader = LeetCodeDownloader(
        leetcode_session=args.leetcode_session,
        csrftoken=args.csrftoken,
        output_dir=args.output_dir,
        timeout=args.timeout,
    )

    try:
        summary = downloader.run()
    except LeetCodeDownloaderError as exc:
        LOGGER.error("Backup failed: %s", exc)
        return 1

    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

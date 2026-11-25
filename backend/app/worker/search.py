"""Retrieve traceable source passages through Tavily's search API."""

import re
from datetime import UTC, datetime
from ipaddress import ip_address
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.worker.ai import AIError

SEARCH_URL = "https://api.tavily.com/search"
MIN_PASSAGES = 5
MAX_PASSAGES = 16


def _source_url(value: str) -> tuple[str, str] | None:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed.port not in (443, None)
            or "." not in host
            or host.endswith((".localhost", ".local", ".internal", ".test", ".invalid"))
        ):
            return None
        try:
            ip_address(host)
        except ValueError:
            return urlunsplit(parsed._replace(fragment="")), host.removeprefix("www.")
    except ValueError:
        pass
    return None


def _squash(value: str) -> str:
    return " ".join(value.split())


class TavilySearch:
    def __init__(self, api_key: str, timeout: float, transport: httpx.BaseTransport | None = None):
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport

    def passages(self, queries: list[str]) -> list[dict]:
        if not self._api_key:
            raise AIError("SEARCH_UNAVAILABLE", "资料检索暂未配置，请稍后再试。", False)
        with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
            candidates = [item for query in queries[:2] for item in self._search(client, query)]

        candidates.sort(key=lambda item: item["score"], reverse=True)
        passages: list[dict] = []
        seen: set[str] = set()
        used_urls: set[str] = set()
        verified_at = datetime.now(UTC).date().isoformat()
        for item in candidates:
            url, host = item["source"]
            if url in used_urls:
                continue
            used_urls.add(url)
            for excerpt in item["passages"]:
                if excerpt in seen:
                    continue
                seen.add(excerpt)
                passages.append(
                    {
                        "evidenceId": f"web-{len(passages) + 1:02d}",
                        "text": excerpt,
                        "sourceTitle": item["title"],
                        "sourceOrganization": host,
                        "sourceUrl": url,
                        "verifiedAt": verified_at,
                    }
                )
                if len(passages) == MAX_PASSAGES:
                    return passages
        return passages

    def _search(self, client: httpx.Client, query: str) -> list[dict]:
        try:
            response = client.post(
                SEARCH_URL,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "query": query,
                    "search_depth": "advanced",
                    "max_results": 6,
                    "chunks_per_source": 3,
                    "include_raw_content": "text",
                    "include_answer": False,
                    "max_age_hours": 24,
                },
            )
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict) or not isinstance(body.get("results"), list):
                raise TypeError("Invalid search response")
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            raise AIError(
                "SEARCH_UNAVAILABLE",
                "暂时无法检索资料，请稍后重试。",
                status in (408, 429, 434) or status >= 500,
            ) from None
        except (httpx.RequestError, TypeError, ValueError):
            raise AIError("SEARCH_UNAVAILABLE", "暂时无法检索资料，请稍后重试。", True) from None

        candidates = []
        for result in body["results"][:6]:
            if not isinstance(result, dict):
                continue
            url, title = result.get("url"), result.get("title")
            raw, content, score = (
                result.get("raw_content"),
                result.get("content"),
                result.get("score"),
            )
            if not all(isinstance(field, str) for field in (url, title, raw, content)):
                continue
            source = _source_url(url) if len(url) <= 2048 else None
            if (
                not source
                or not title.strip()
                or not isinstance(score, (int, float))
                or score < 0.35
            ):
                continue
            normalized = _squash(raw[:250_000])
            chunks = [_squash(part) for part in re.split(r"\s*\[\.\.\.\]\s*", content[:6000])]
            passages = [
                chunk[:1600] for chunk in chunks if len(chunk) >= 70 and chunk in normalized
            ]
            if passages:
                candidates.append(
                    {
                        "title": title.strip()[:200],
                        "source": source,
                        "passages": passages[:3],
                        "score": score,
                    }
                )
        return candidates

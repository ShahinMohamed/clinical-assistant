"""Search approved websites and return extracted web documents."""

import hashlib
import ipaddress
import socket
from datetime import UTC, datetime
from time import monotonic
from urllib.parse import urljoin, urlsplit

import httpx
import trafilatura
from langchain_core.documents import Document

from rag.evidence_common import required


ALLOWED_HOSTS = {
    "who.int": "World Health Organization",
    "www.who.int": "World Health Organization",
    "nice.org.uk": "NICE",
    "www.nice.org.uk": "NICE",
    "cdc.gov": "CDC",
    "www.cdc.gov": "CDC",
}

SEARCH_DOMAINS = ["who.int", "nice.org.uk", "cdc.gov"]

MAX_RESULTS = 3
MAX_BYTES = 2_000_000


def web_settings():
    return {
        "mode": "live",
        "provider": "tavily",
        "domains": SEARCH_DOMAINS,
        "allowed_hosts": sorted(ALLOWED_HOSTS),
        "max_results": MAX_RESULTS,
        "max_bytes_per_source": MAX_BYTES,
        "supported_content": "HTML only",
        "search_snippets_are_evidence": False,
        "medical_fact_verification": False,
    }


def validate_url(url, resolve=False):
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()

    if (
        parsed.scheme != "https"
        or host not in ALLOWED_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.port not in (None, 443)
    ):
        raise ValueError("URL is not on an approved HTTPS host.")

    if resolve:
        addresses = {
            entry[4][0]
            for entry in socket.getaddrinfo(
                host,
                443,
                type=socket.SOCK_STREAM,
            )
        }

        if not addresses or any(
            not ipaddress.ip_address(address).is_global
            for address in addresses
        ):
            raise ValueError("Host did not resolve to public IP addresses.")

    return parsed._replace(fragment="").geturl()


def fetch_html(client, url):
    deadline = monotonic() + 60

    for _ in range(4):
        if monotonic() > deadline:
            raise TimeoutError("Source download exceeded its time budget.")

        url = validate_url(url, resolve=True)

        with client.stream("GET", url) as response:
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")

                if not location:
                    raise ValueError("Redirect has no destination.")

                url = urljoin(url, location)
                continue

            response.raise_for_status()

            content_type = (
                response.headers.get("content-type", "")
                .split(";")[0]
                .strip()
                .lower()
            )

            if content_type not in {
                "text/html",
                "application/xhtml+xml",
            }:
                raise ValueError("Only HTML sources are supported.")

            content = bytearray()

            for part in response.iter_bytes(chunk_size=65_536):
                content.extend(part)

                if len(content) > MAX_BYTES:
                    raise ValueError("Source exceeds the download limit.")

                if monotonic() > deadline:
                    raise TimeoutError(
                        "Source download exceeded its time budget."
                    )

            raw = bytes(content)

            return (
                raw.decode(
                    response.encoding or "utf-8",
                    errors="replace",
                ),
                str(response.url),
                hashlib.sha256(raw).hexdigest(),
            )

    raise ValueError("Too many redirects.")


def retrieve_web(question):
    """Return full extracted pages, not ranked chunks."""

    documents = []
    skipped_sources = []
    seen_urls = set()

    with httpx.Client(
        timeout=httpx.Timeout(15, connect=5),
        follow_redirects=False,
        trust_env=False,
        headers={
            "User-Agent": "ClinicalEvidenceResearchPrototype/1.0",
        },
    ) as client:
        response = client.post(
            "https://api.tavily.com/search",
            headers={
                "Authorization": f"Bearer {required('TAVILY_API_KEY')}",
            },
            json={
                "query": question,
                "topic": "general",
                "search_depth": "basic",
                "max_results": MAX_RESULTS,
                "include_domains": SEARCH_DOMAINS,
                "include_domains_mode": "restrict",
                "include_answer": False,
                "include_raw_content": False,
            },
        )
        response.raise_for_status()

        results = response.json().get("results", [])

        for result in results[:MAX_RESULTS]:
            url = result.get("url", "")

            try:
                # Search snippets are not used as evidence.
                html, final_url, checksum = fetch_html(client, url)

                if final_url in seen_urls:
                    continue

                seen_urls.add(final_url)

                text = trafilatura.extract(
                    html,
                    include_comments=False,
                    include_tables=True,
                )

                if not text or len(text.strip()) < 120:
                    raise ValueError("Insufficient extracted text.")

                if "\ufffd" in text:
                    raise ValueError("Extracted text has decoding errors.")

                metadata = trafilatura.extract_metadata(
                    html,
                    default_url=final_url,
                )

                host = urlsplit(final_url).hostname.lower()
                source_id = (
                    "web-"
                    + hashlib.sha256(final_url.encode()).hexdigest()[:16]
                )

                documents.append(
                    Document(
                        page_content=text,
                        metadata={
                            "origin": "web",
                            "source_id": source_id,
                            "title": (
                                metadata.title
                                if metadata and metadata.title
                                else final_url
                            ),
                            "publisher": ALLOWED_HOSTS[host],
                            "canonical_url": final_url,
                            "publication_date": None,
                            "source_date": (
                                metadata.date if metadata else None
                            ),
                            "source_date_kind": "unspecified_metadata_date",
                            "retrieved_at": datetime.now(UTC).isoformat(),
                            "geographic_scope": (
                                "Not established; verify source applicability."
                            ),
                            "pdf_page_start": None,
                            "pdf_page_end": None,
                            "checksum": checksum,
                        },
                    )
                )

            except Exception as error:
                skipped_sources.append({
                    "url": url,
                    "error": type(error).__name__,
                })

    return {
        "documents": documents,
        "trace": {
            "origin": "web",
            "mode": "live",
            "query": question,
            "fetched_document_count": len(documents),
            "sources": [
                {
                    "url": document.metadata["canonical_url"],
                    "checksum": document.metadata["checksum"],
                    "retrieved_at": document.metadata["retrieved_at"],
                }
                for document in documents
            ],
            "skipped_sources": skipped_sources,
        },
    }
"""Server-only response redaction and a shared PostgreSQL request budget."""

import hashlib
import hmac
import ipaddress
import os
from datetime import UTC, datetime
from urllib.parse import unquote, urlsplit


class ResearchLimit(Exception):
    pass


def redact_secrets(value):
    secrets = set()
    for name, item in os.environ.items():
        if item and any(part in name.upper() for part in ("KEY", "SECRET", "TOKEN", "PASSWORD", "DATABASE_URL")):
            if len(item) >= 8:
                secrets.add(item)
            if "DATABASE_URL" in name.upper():
                password = urlsplit(item).password
                if password:
                    secrets.add(password)
                    secrets.add(unquote(password))

    def clean(item):
        if isinstance(item, str):
            for secret in sorted(secrets, key=len, reverse=True):
                item = item.replace(secret, "[redacted]")
            return item
        if isinstance(item, dict):
            return {str(key): clean(child) for key, child in item.items()}
        if isinstance(item, (tuple, list)):
            return [clean(child) for child in item]
        return item

    return clean(value)


def consume_research_budget(headers):
    """Fail closed on Vercel; local development does not spend hosted quota."""
    if os.getenv("VERCEL") != "1":
        return
    import psycopg

    secret = os.environ.get("APP_RATE_LIMIT_SECRET", "")
    if len(secret) < 32:
        raise RuntimeError("The hosted request budget is not configured.")
    # Vercel overwrites this header at its edge; do not trust it on other hosts.
    ip = str(ipaddress.ip_address(headers.get("X-Forwarded-For", "").split(",")[0].strip()))
    client = hmac.new(secret.encode(), ip.encode(), hashlib.sha256).hexdigest()
    now = datetime.now(UTC)
    minute = now.replace(second=0, microsecond=0)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    limits = [
        ("global", day, int(os.getenv("RESEARCH_DAILY_LIMIT", "50"))),
        (client, day, int(os.getenv("RESEARCH_CLIENT_DAILY_LIMIT", "10"))),
        (client, minute, 1),
    ]
    with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=5) as connection:
        connection.execute("SET LOCAL statement_timeout = '5s'")
        for bucket, window, limit in limits:
            row = connection.execute("""
                INSERT INTO app_private.research_budget (bucket, window_start, requests)
                VALUES (%s, %s, 1)
                ON CONFLICT (bucket, window_start) DO UPDATE
                SET requests = research_budget.requests + 1
                WHERE research_budget.requests < %s
                RETURNING requests
            """, (bucket, window, limit)).fetchone() if limit > 0 else None
            if row is None:
                # Raising rolls back every increment in this transaction.
                raise ResearchLimit("Demo limit reached. Try again later.")
        connection.execute("DELETE FROM app_private.research_budget WHERE window_start < now() - interval '2 days'")

"""Process-wide log redaction.

Two confirmed leak vectors for the upstream API key (spike/probes.md):
inbound — the gateway's 401 body echoes an unauthorized key verbatim in
`WWW-Authenticate`; outbound — nothing today logs the `KeyId` request
header, but nothing should ever be trusted to remember that by convention
alone. Rather than audit every call site, every log record's rendered
message is scrubbed for known secret values before it reaches a handler.

Filters attached to a *Logger* only run for records logged directly through
that logger, not for records propagating up from children (e.g. the
`httpx` logger) — so this must be attached to the root logger's *handler*,
not the root logger itself, to actually cover every logger in the process.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

_REDACTED = "***REDACTED***"


class SecretRedactionFilter(logging.Filter):
    def __init__(self, secrets: Iterable[str]):
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def filter(self, record: logging.LogRecord) -> bool:
        if not self._secrets:
            return True
        message = record.getMessage()
        redacted = message
        for secret in self._secrets:
            redacted = redacted.replace(secret, _REDACTED)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def configure_logging(*secrets: str, level: int = logging.INFO) -> SecretRedactionFilter:
    """Set up the process's only logging handler with the redaction filter
    attached, so every logger (including third-party ones like httpx)
    passes through it regardless of where it's created."""
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    redaction_filter = SecretRedactionFilter(secrets)
    handler.addFilter(redaction_filter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]
    return redaction_filter

"""Secret masking filter for Python logging.

Redacts hex private keys and other sensitive patterns from log records
before they are emitted to any handler.
"""
from __future__ import annotations

import logging
import re

# Matches 0x + 64 hex chars (Ethereum private keys)
_HEX_KEY_RE = re.compile(r'0x[a-fA-F0-9]{64}')

# Matches bare 64-char hex strings (private keys without 0x prefix)
_BARE_HEX_RE = re.compile(r'(?<![a-fA-F0-9])[a-fA-F0-9]{64}(?![a-fA-F0-9])')


class SecretFilter(logging.Filter):
    """Redacts sensitive patterns from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = _HEX_KEY_RE.sub('[REDACTED_KEY]', record.msg)
            record.msg = _BARE_HEX_RE.sub('[REDACTED_HEX]', record.msg)
        if record.args:
            record.args = tuple(
                _HEX_KEY_RE.sub('[REDACTED_KEY]', str(a))
                if isinstance(a, str) else a
                for a in (record.args if isinstance(record.args, tuple) else (record.args,))
            )
        return True


def install_secret_filter() -> None:
    """Install SecretFilter on the root logger."""
    logging.getLogger().addFilter(SecretFilter())

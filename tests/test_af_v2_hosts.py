"""The production-host rule, enforced.

Runtime code has one host definition. The launched production host is pinned,
and the former preview deployment is forbidden because it still answers with a
stale market universe instead of failing loudly.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

#: Vendored reference material is checked separately because it is pinned
#: upstream content and deliberately kept byte-identical.
EXEMPT_DIRS = {
    "AFTERMATH_SKILLS_REF",
    ".git",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "data",
}

#: This file constructs the retired hostname in pieces so repository-wide
#: literal greps stay useful while the guard can still detect it elsewhere.
EXEMPT_FILES = {
    "tests/test_af_v2_hosts.py",
}

TEXT_SUFFIXES = {".py", ".md", ".json", ".toml", ".yaml", ".yml", ".mjs", ".js", ".ts", ".txt", ".example", ".sh"}

PRODUCTION_BASE_URL = "https://aftermath.finance"
RETIRED_PREVIEW_HOST = "v2-" + "preview." + "aftermath.finance"


def _candidate_files():
    for path in REPO.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(REPO)
        if any(part in EXEMPT_DIRS for part in rel.parts):
            continue
        if str(rel) in EXEMPT_FILES:
            continue
        if path.suffix not in TEXT_SUFFIXES:
            continue
        yield rel, path


def test_no_retired_preview_host_in_repository_text():
    """No local source, config, example, or documentation may use the old host."""
    offenders = []
    for rel, path in _candidate_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if RETIRED_PREVIEW_HOST in line:
                offenders.append(f"{rel}:{lineno}: {line.strip()[:100]}")

    assert not offenders, (
        "References to the retired preview host found. The production host is "
        f"{PRODUCTION_BASE_URL} and the runtime default must come from "
        "cli/af/config.py:AF_API_BASE_URL_DEFAULT.\n  "
        + "\n  ".join(offenders)
    )


def test_vendored_skills_use_the_production_host():
    """Pinned upstream API references must agree with the launched host."""
    vendored = REPO / "AFTERMATH_SKILLS_REF"
    if not vendored.exists():
        pytest.skip("vendored skills not present")

    production_hits = 0
    retired_hits = []
    for path in vendored.rglob("*"):
        if path.is_file() and path.suffix in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8", errors="ignore")
            production_hits += text.count(PRODUCTION_BASE_URL)
            if RETIRED_PREVIEW_HOST in text:
                retired_hits.append(str(path.relative_to(REPO)))

    assert production_hits > 0, "vendored skills contain no production API references"
    assert not retired_hits, f"vendored skills reference the retired preview host: {retired_hits}"


def test_exactly_one_host_definition():
    """The production literal appears in exactly one non-test Python module."""
    definitions = []
    for rel, path in _candidate_files():
        if path.suffix != ".py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if PRODUCTION_BASE_URL in text:
            definitions.append(str(rel))

    non_test = [d for d in definitions if not d.startswith("tests/")]
    assert non_test == ["cli/af/config.py"], (
        f"the production host literal must be defined only in cli/af/config.py, found in: {non_test}"
    )


def test_base_url_is_production_by_default(monkeypatch):
    from cli.af.config import AF_API_BASE_URL, AF_API_BASE_URL_DEFAULT

    monkeypatch.delenv("AF_API_BASE_URL", raising=False)
    assert AF_API_BASE_URL() == PRODUCTION_BASE_URL
    assert AF_API_BASE_URL_DEFAULT == PRODUCTION_BASE_URL


def test_base_url_is_overridable_without_editing_source(monkeypatch):
    from cli.af.config import AF_API_BASE_URL

    monkeypatch.setenv("AF_API_BASE_URL", "https://example.invalid/")
    assert AF_API_BASE_URL() == "https://example.invalid"


def _code_only(source: str) -> str:
    """Strip comments and string literals, leaving executable code.

    Prose *describing* what was removed is fine and in fact desirable; an
    import, host or env var is not. Scanning raw text cannot tell the two
    apart, so the docstrings and comments come out first.
    """
    import io
    import tokenize

    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            out.append(f"{tok.start[0]}:{tok.string}")
    except (tokenize.TokenError, IndentationError):  # pragma: no cover
        return source
    return "\n".join(out)


def test_no_hyperliquid_in_the_aftermath_execution_path():
    """The adapter package must contain zero Hyperliquid execution surface.

    Aftermath is not a Hyperliquid proxy: HL is removed as a venue, not
    re-pointed underneath. Zero venue API calls, hosts, SDKs or env vars may
    survive anywhere in the Aftermath execution path.
    """
    banned = re.compile(
        r"(hyperliquid|HL_PRIVATE_KEY|HL_TESTNET|HL_ACCOUNT_ADDRESS|hl_adapter|"
        r"hl_proxy|HLFill|DirectHLProxy|destinationDex|sendAsset|marginPct)",
        re.IGNORECASE,
    )
    offenders = []
    for path in sorted((REPO / "cli" / "af").rglob("*.py")):
        for entry in _code_only(path.read_text(encoding="utf-8")).splitlines():
            lineno, _, tok = entry.partition(":")
            if tok and banned.search(tok):
                offenders.append(f"{path.relative_to(REPO)}:{lineno}: {tok[:90]}")
    assert not offenders, "Hyperliquid surface found in the Aftermath adapter:\n  " + "\n  ".join(offenders)


def test_adapter_imports_nothing_from_a_hyperliquid_module():
    """Structural check: no import in cli/af/ resolves to an HL module."""
    import ast

    offenders = []
    for path in sorted((REPO / "cli" / "af").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                mods = [node.module or ""]
            for m in mods:
                if re.search(r"hyperliquid|hl_adapter|hl_proxy", m, re.IGNORECASE):
                    offenders.append(f"{path.relative_to(REPO)}:{node.lineno}: imports {m}")
    assert not offenders, "Aftermath adapter imports a Hyperliquid module:\n  " + "\n  ".join(offenders)

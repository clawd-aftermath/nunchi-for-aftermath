"""The host rule, enforced.

The Aftermath relaunch domain will change. A repository with the hostname
smeared across forty files is a repository that breaks silently on that day --
silently, because the retired host does not error, it simply stops being the
API.

So: exactly one host definition, and a test that fails the build if a second
one appears.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent

#: Vendored reference material is exempt -- it is pinned upstream content and
#: is deliberately kept byte-identical. See AFTERMATH_SKILLS_REF/README-DELTA.md.
EXEMPT_DIRS = {
    "AFTERMATH_SKILLS_REF",
    ".git",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "data",
}

#: Files allowed to name the bare host, because explaining that it is dead is
#: their entire job.
EXEMPT_FILES = {
    "tests/test_af_v2_hosts.py",  # this file names the dead host to detect it
}

TEXT_SUFFIXES = {".py", ".md", ".json", ".toml", ".yaml", ".yml", ".mjs", ".js", ".ts", ".txt", ".example", ".sh"}

#: The dead v1 host: `aftermath.finance` NOT preceded by `v2-preview.`.
DEAD_HOST = re.compile(r"(?<!v2-preview\.)(?<!testnet\.)\baftermath\.finance\b")


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


def test_no_dead_v1_host_outside_vendored_skills():
    """No file may reference the retired `aftermath.finance` API host."""
    offenders = []
    for rel, path in _candidate_files():
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.splitlines(), 1):
            if DEAD_HOST.search(line):
                offenders.append(f"{rel}:{lineno}: {line.strip()[:100]}")

    assert not offenders, (
        "References to the RETIRED v1 host `aftermath.finance` found. "
        "The live host is https://v2-preview.aftermath.finance and must come "
        "from cli/af/config.py:AF_API_BASE_URL_DEFAULT.\n  "
        + "\n  ".join(offenders)
    )


def test_vendored_skills_still_carry_the_trap():
    """The vendored skills DO name the dead host -- that is expected and documented.

    If this ever goes to zero, upstream fixed their URLs and README-DELTA.md
    should be updated to say so.
    """
    vendored = REPO / "AFTERMATH_SKILLS_REF"
    if not vendored.exists():
        pytest.skip("vendored skills not present")

    hits = 0
    for path in vendored.rglob("*"):
        if path.is_file() and path.suffix in TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8", errors="ignore")
            hits += len(DEAD_HOST.findall(text))

    assert hits > 0, (
        "The vendored skills no longer reference the dead host. Upstream may "
        "have fixed their URLs -- re-sync and update AFTERMATH_SKILLS_REF/README-DELTA.md."
    )


def test_exactly_one_host_definition():
    """The default host literal appears in exactly one place in the source tree."""
    definitions = []
    for rel, path in _candidate_files():
        if path.suffix != ".py":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if "v2-preview.aftermath.finance" in text:
            definitions.append(str(rel))

    non_test = [d for d in definitions if not d.startswith("tests/")]
    assert non_test == ["cli/af/config.py"], (
        f"the V2 host literal must be defined only in cli/af/config.py, found in: {non_test}"
    )


def test_base_url_is_v2_preview_by_default(monkeypatch):
    from cli.af.config import AF_API_BASE_URL, AF_API_BASE_URL_DEFAULT

    monkeypatch.delenv("AF_API_BASE_URL", raising=False)
    assert AF_API_BASE_URL() == "https://v2-preview.aftermath.finance"
    assert AF_API_BASE_URL_DEFAULT == "https://v2-preview.aftermath.finance"


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

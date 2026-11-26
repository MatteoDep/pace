"""Drift tests: the coaching skill must match `pace schema` as built."""

import re
from pathlib import Path

from pace.commands.schema import build_schema
from pace.diary import PROFILE_KEYS, WEIGHT_DAYS_DEFAULT

ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = ROOT / ".claude" / "skills" / "pace"
SKILL = SKILL_DIR / "SKILL.md"
REF = SKILL_DIR / "reference.md"
COACHING = SKILL_DIR / "coaching.md"
DASHBOARD = SKILL_DIR / "dashboard.md"
FILES = (SKILL, REF, COACHING, DASHBOARD)

DESCRIPTION = (
    "Use when the user talks about their running or other training, their Garmin data (runs, sleep, "
    "resting HR, weight, recovery), a training plan, thresholds or zones, how a workout went, or wants "
    "coaching, a check-in, a chart, or a workout on their watch. Drives the `pace` CLI and keeps the "
    "athlete diary, training plan and zones."
)

# Spans that are not schema commands: `pace schema` describes itself, login is human-only.
ALLOWED = {"schema": set(), "auth login": set()}
FLAG_RE = re.compile(r"--[a-z][a-z0-9-]*")
SPAN_RE = re.compile(r"`(pace [^`\n]*)`")


def _schema() -> dict:
    return build_schema(None)


def _commands() -> dict[str, dict]:
    return {c["name"]: c for c in _schema()["commands"]}


def _section(text: str, heading: str) -> str:
    start = text.index(f"\n{heading}\n")
    rest = text[start + len(heading) + 2:]
    end = re.search(r"^## ", rest, re.MULTILINE)
    return rest[: end.start()] if end else rest


def _spans(text: str) -> list[str]:
    """Inline `pace ...` spans plus `pace ...` lines inside fenced or indented code blocks."""
    spans = SPAN_RE.findall(text)
    in_fence = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        stripped = line.strip()
        if (in_fence or line.startswith("    ")) and stripped.startswith("pace "):
            spans.append(stripped)
    return spans


def _head(span: str) -> list[str]:
    """Tokens after `pace`, up to a bare `--` (end of options): what follows is free text."""
    tokens = span.split()[1:]
    return tokens[: tokens.index("--")] if "--" in tokens else tokens


def _resolve(span: str, names: set[str]) -> str | None:
    tokens = _head(span)
    for n in range(len(tokens), 0, -1):
        cand = " ".join(tokens[:n])
        if cand in names or cand in ALLOWED:
            return cand
    return None


def _mentions(text: str, name: str) -> bool:
    return re.search(r"`pace " + re.escape(name) + r"(?=[ `])", text) is not None


def test_files_exist_ascii_frontmatter() -> None:
    for p in FILES:
        text = p.read_text(encoding="utf-8")
        assert text.isascii(), f"{p.name} has non-ASCII: {[c for c in text if not c.isascii()][:5]}"
    skill = SKILL.read_text()
    assert skill.startswith(f"---\nname: pace\ndescription: {DESCRIPTION}\n---\n")


def test_every_group_and_command_in_reference() -> None:
    ref = REF.read_text()
    for group in _schema()["groups"]:
        assert f"| {group} |" in ref or _mentions(ref, group), group
    for name in _commands():
        assert _mentions(ref, name), name


def _span_errors(span: str, cmds: dict[str, dict]) -> list[str]:
    """Problems with one span: unknown command, or a flag the resolved command does not take.

    Only the command's own `flags` (plus --help) count: `global_flags` describes the root and the
    shaping flags, which every command that takes them already lists.
    """
    tokens = span.split()
    groups = {n.split()[0] for n in cmds}
    if len(tokens) == 3 and tokens[1] in groups and tokens[2] == "--help":
        return []
    name = _resolve(span, set(cmds))
    if name is None:
        return [f"unknown command in `{span}`"]
    allowed = (ALLOWED[name] if name in ALLOWED else set(cmds[name]["flags"])) | {"--help"}
    return [f"{flag} not a flag of `pace {name}` in `{span}`"
            for flag in FLAG_RE.findall(" ".join(_head(span))) if flag not in allowed]


def test_pace_spans_resolve() -> None:
    cmds = _commands()
    assert not (ALLOWED.keys() & cmds.keys()), "allowlisted command now in the schema: drop it from ALLOWED"
    bad = [f"{p.name}: {e}" for p in FILES for span in _spans(p.read_text()) for e in _span_errors(span, cmds)]
    assert not bad, "\n".join(bad)


def test_span_checker_catches_drift() -> None:
    cmds = _commands()
    names = set(cmds)
    assert _resolve("pace diary set-due 4 --clear", names) == "diary set-due"
    assert _resolve("pace diary set checkin.last now", names) == "diary set"
    assert _resolve("pace plan block add --name x", names) == "plan block add"
    assert _resolve("pace diary renamed", names) is None
    assert _resolve("pace -- diary set", names) is None
    must_fail = ["pace diary show --from x", "pace diary show --sinse x", "pace plan status --fields all",
                 "pace diary renamed", "pace zones push --force"]
    for span in must_fail:
        assert _span_errors(span, cmds), span
    must_pass = ["pace diary show --since x --kind feedback", "pace diary add --kind note -- --from text",
                 "pace plan --help", "pace zones push --dry-run", "pace activity list --days 7 --fields all"]
    for span in must_pass:
        assert not _span_errors(span, cmds), span


def test_writes_section_complete() -> None:
    writes = _section(SKILL.read_text(), "## Writes")
    missing = [n for n, c in _commands().items()
               if (c.get("write") or c.get("local_write")) and not _mentions(writes, n)]
    assert not missing, missing


def test_session_start_spans() -> None:
    skill = SKILL.read_text()
    start = _section(skill, "## Session start")
    for cmd in ("pace auth status", "pace diary show", "pace plan status"):
        assert f"`{cmd}" in start or f"    {cmd}" in start, cmd
    assert "pace diary set checkin.last now" in _section(skill, "## Full check-in")


def test_constants_match() -> None:
    skill = SKILL.read_text()
    intake = _section(skill, "## Intake")
    line = next(ln for ln in intake.splitlines() if ln.startswith("Profile keys:"))
    listed = re.findall(r"`([a-z_.]+)`", line)
    assert listed == [k for k in PROFILE_KEYS if k not in ("checkin.last", "dashboard.url")]
    defaults = re.findall(r"`checkin\.weight_days`[^\n]*?default (\d+)", skill)
    assert defaults and {int(d) for d in defaults} == {WEIGHT_DAYS_DEFAULT}
    assert _schema()["diary"]["checkin_weight_days_default"] == WEIGHT_DAYS_DEFAULT


GRADES = ("strong", "moderate", "weak/expert opinion")


def _coaching_sections() -> list[tuple[str, str]]:
    text = COACHING.read_text()
    parts = re.split(r"^## (.+)$", text, flags=re.MULTILINE)
    return list(zip(parts[1::2], parts[2::2], strict=True))


def test_coaching_sections() -> None:
    sections = _coaching_sections()
    titles = [t for t, _ in sections]
    assert "Thresholds and zones" in titles and len(sections) >= 10, titles
    for title, body in sections:
        lines = body.splitlines()
        evidence = [ln for ln in lines if ln.startswith("Evidence: ")]
        assert evidence, f"{title}: no Evidence line"
        for ln in evidence:
            assert any(g in ln for g in GRADES), f"{title}: {ln}"
        heads = [i for i, ln in enumerate(lines) if ln.startswith("Sources:")]
        assert heads, f"{title}: no Sources"
        for i in heads:
            if lines[i] == "Sources: none verified":
                continue
            assert lines[i] == "Sources:", f"{title}: {lines[i]}"
            items = []
            for ln in lines[i + 1:]:
                if not ln.startswith("- "):
                    break
                items.append(ln)
            assert items, f"{title}: empty Sources list"
            for ln in items:
                assert re.search(r"https://\S+", ln), f"{title}: no DOI/URL: {ln}"
                assert ln.endswith(("(full text)", "(abstract)")), f"{title}: no marker: {ln}"


def test_skill_md_size() -> None:
    assert len(SKILL.read_text().splitlines()) <= 250


def test_settings_rules() -> None:
    skill = SKILL.read_text()
    flat = " ".join(skill.split())
    assert "0 means never ask" in flat
    start = _section(skill, "## Session start")
    assert "`checkin.gear`" in start and "pace activity gear" in start
    assert "measurementSystem" in _section(skill, "## Settings")
    assert "untrusted" in " ".join(_section(skill, "## Settings").split())
    assert "never gaps" in " ".join(_section(skill, "## Intake").split())
    writes = " ".join(_section(skill, "## Writes").split())
    assert "pace gear link" in writes and "naming the shoe" in writes
    dash = (SKILL.parent / "dashboard.md").read_text()
    assert "data/settings-pending" in dash and "data/settings-current" in dash
    assert "deletes" in dash


def test_dashboard_uses_export() -> None:
    dash = DASHBOARD.read_text()
    assert "pace export" in dash and "pace load --export" not in dash
    assert "## Add a view" in dash and "bolted on" not in dash
    assert "dash.loader" in dash and "pace export --check" in dash

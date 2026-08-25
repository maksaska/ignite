#!/usr/bin/env python3
"""Shared plumbing for the incident kit: the site-pattern overlay and parse health.

Two jobs.

1. THE OVERLAY. Site log formats vary and cannot all be anticipated. Rather than have a
   model edit the parsers, every script loads `site-patterns.json` and merges its patterns
   AHEAD of the built-in ones. That file is where fixes go: reviewable, revertable, and it
   cannot break the code paths the selftest covers.

2. PARSE HEALTH. The kit's worst failure mode is a parser that half-works: it produces a
   thin but plausible digest and nothing downstream notices. Every digest therefore reports
   how much of each file it actually understood, and the wording for a degraded file is
   defined here once so it cannot drift between scripts.

   The distinction that matters:

       parsed well + nothing matched  -> a real negative finding
       parsed badly                   -> a TOOLING problem, never a finding

Stdlib only.
"""

import json
import re
from pathlib import Path

# --------------------------------------------------------------------------- #
# Overlay
# --------------------------------------------------------------------------- #

OVERLAY_FILENAME = "site-patterns.json"

SECTIONS = (
    "file_signatures",     # {kind: [[regex, weight], ...]}      -> identify.py
    "ignite_line_layouts",  # ["regex with named groups"]        -> ignite_timeline.py
    "ignite_events",       # [[category, severity, regex], ...]  -> ignite_timeline.py
    "os_patterns",         # [[label, severity, regex, why], ...] -> os_digest.py
    "nmon_aliases",        # {canonical_section: [alias, ...]}   -> nmon_digest.py
    "safepoint_formats",   # ["regex with named groups"]         -> gc_digest.py
)

TEMPLATE = {
    "_readme": [
        "Site-local pattern overlay for the Ignite incident kit.",
        "Patterns here are tried BEFORE the built-in ones. Edit this file rather than the",
        "scripts. After any change run BOTH:  preflight.py (verdict must improve)  and",
        "selftest.py (must stay green - that proves you did not break the known-good path).",
        "",
        "ignite_line_layouts: regex with named groups date, time, ms, level, thread, cat, msg.",
        "ignite_events:       [category, severity 1-3, regex]",
        "os_patterns:         [label, severity 1-3, regex, why-it-matters]",
        "file_signatures:     {kind: [[regex, weight], ...]}  - weights sum; 6 classifies",
        "nmon_aliases:        {canonical_section: [alias, ...]}",
        "safepoint_formats:   regex with named groups ttsp, at, total (plus optional unit)",
    ],
    "file_signatures": {},
    "ignite_line_layouts": [],
    "ignite_events": [],
    "os_patterns": [],
    "nmon_aliases": {},
    "safepoint_formats": [],
}


class OverlayError(Exception):
    """Raised with a message aimed at whoever wrote the overlay."""


def find_overlay(explicit=None, near=None):
    """Locate the overlay: an explicit path, else site-patterns.json beside `near`
    (normally the analysis workspace holding inventory.json).

    Deliberately does NOT fall back to the current directory: an overlay that loads
    depending on where you happened to run from would change results invisibly."""
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise OverlayError("--patterns %s does not exist" % explicit)
        return p
    if near:
        base = Path(near)
        cand = (base if base.is_dir() else base.parent) / OVERLAY_FILENAME
        if cand.is_file():
            return cand
    return None


def load_overlay(explicit=None, near=None):
    """Return (overlay_dict, path_or_None). Unknown sections and bad regexes are fatal --
    a silently ignored overlay is worse than no overlay, because the author believes the
    fix is in place."""
    path = find_overlay(explicit, near)
    empty = {s: ([] if s not in ("file_signatures", "nmon_aliases") else {}) for s in SECTIONS}
    if path is None:
        return empty, None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise OverlayError("%s is not valid JSON: %s" % (path, exc))
    if not isinstance(raw, dict):
        raise OverlayError("%s must contain a JSON object" % path)

    out = dict(empty)
    for key, val in raw.items():
        if key.startswith("_"):
            continue
        if key not in SECTIONS:
            raise OverlayError(
                "%s: unknown section %r. Known sections: %s"
                % (path, key, ", ".join(SECTIONS)))
        out[key] = val

    # Validate every regex up front so failures name the offending pattern.
    for rx in out["ignite_line_layouts"] + out["safepoint_formats"]:
        _compile(rx, path)
    for row in out["ignite_events"]:
        if len(row) != 3:
            raise OverlayError("%s: ignite_events entries must be [category, severity, regex], "
                               "got %r" % (path, row))
        _compile(row[2], path)
    for row in out["os_patterns"]:
        if len(row) not in (3, 4):
            raise OverlayError("%s: os_patterns entries must be [label, severity, regex] or "
                               "[label, severity, regex, why], got %r" % (path, row))
        _compile(row[2], path)
    for kind, pats in out["file_signatures"].items():
        for entry in pats:
            if len(entry) != 2:
                raise OverlayError("%s: file_signatures[%s] entries must be [regex, weight], "
                                   "got %r" % (path, kind, entry))
            _compile(entry[0], path)
    return out, path


def _compile(rx, path):
    try:
        return re.compile(rx)
    except re.error as exc:
        raise OverlayError("%s: bad regex %r - %s" % (path, rx, exc))


def overlay_note(path, overlay):
    """One-line Markdown note naming the overlay in use, for a digest header."""
    if not path:
        return "_No site-pattern overlay in use._"
    counts = []
    for s in SECTIONS:
        v = overlay.get(s)
        n = len(v) if v is not None else 0
        if n:
            counts.append("%s=%d" % (s, n))
    return ("**Site-pattern overlay active:** `%s` (%s). Patterns from it were tried before "
            "the built-ins." % (path, ", ".join(counts) or "empty"))


# --------------------------------------------------------------------------- #
# Parse health
# --------------------------------------------------------------------------- #

OK, DEGRADED, FAILED = "OK", "DEGRADED", "FAILED"

RATE_OK = 0.95        # at or above this, the parser understood the file
RATE_DEGRADED = 0.50  # below this, treat the file as unread

MAX_SAMPLES = 8


class Health(object):
    """How much of one file a parser actually understood."""

    def __init__(self, name, kind=""):
        self.name = name
        self.kind = kind
        self.lines = 0
        self.parsed = 0        # matched the line grammar
        self.recognised = 0    # matched a content pattern (event, record, ...)
        self.samples = []      # (lineno, verbatim text) for lines that did not parse
        self.note = ""

    def line(self, ok, lineno=None, text=None):
        self.lines += 1
        if ok:
            self.parsed += 1
        elif len(self.samples) < MAX_SAMPLES:
            self.samples.append((lineno, (text or "").rstrip()[:300]))

    @property
    def rate(self):
        return (float(self.parsed) / self.lines) if self.lines else 0.0

    @property
    def verdict(self):
        if self.lines == 0:
            return FAILED
        if self.rate >= RATE_OK:
            return OK
        if self.rate >= RATE_DEGRADED:
            return DEGRADED
        return FAILED

    @property
    def healthy(self):
        return self.verdict == OK

    def as_dict(self):
        return {"file": self.name, "kind": self.kind, "lines": self.lines,
                "parsed": self.parsed, "recognised": self.recognised,
                "rate": round(self.rate, 4), "verdict": self.verdict,
                "samples": [{"line": n, "text": t} for n, t in self.samples],
                "note": self.note}


def worst(healths):
    order = {OK: 0, DEGRADED: 1, FAILED: 2}
    return max([h.verdict for h in healths], key=lambda v: order[v]) if healths else FAILED


def render_health(healths, out, overlay_path=None, overlay=None):
    """Write the mandatory '## Parse health' block. Every digest calls this."""
    w = out.write
    w("## Parse health\n\n")
    if overlay is not None:
        w("%s\n\n" % overlay_note(overlay_path, overlay))
    if not healths:
        w("_No files were read._\n\n")
        return
    w("| file | kind | lines | parsed | rate | recognised | verdict |\n")
    w("|---|---|---|---|---|---|---|\n")
    for h in healths:
        w("| `%s` | %s | %d | %d | %.1f%% | %d | %s |\n"
          % (h.name, h.kind or "-", h.lines, h.parsed, 100.0 * h.rate, h.recognised,
             "**%s**" % h.verdict if h.verdict != OK else OK))
    w("\n")

    bad = [h for h in healths if h.verdict != OK]
    if bad:
        w(degraded_banner(bad))
    for h in bad:
        if not h.samples:
            continue
        w("<details><summary>Lines that did not parse: <code>%s</code></summary>\n\n```\n"
          % h.name)
        for lineno, text in h.samples:
            w("%6s | %s\n" % (lineno if lineno is not None else "?", text))
        w("```\n</details>\n\n")


def degraded_banner(bad):
    """The one wording for a degraded parse, shared by every script."""
    names = ", ".join("`%s` (%s, %.0f%% parsed)" % (h.name, h.verdict, 100.0 * h.rate)
                      for h in bad)
    return (
        "> ### PARSE PROBLEM - READ BEFORE USING ANYTHING BELOW\n>\n"
        "> The parser did not fully understand: %s\n>\n"
        "> **A low parse rate is a tooling problem, never a finding.** Whatever appears below\n"
        "> is incomplete by an unknown amount. In particular, do NOT write that nothing was\n"
        "> found, that a signal was absent, or that an artifact rules something out - the\n"
        "> parser simply could not read it.\n>\n"
        "> Go to `references/90-when-scripts-fail.md` and work the ladder: run the script with\n"
        "> `--diagnose`, then repair via `site-patterns.json`, then re-run `preflight.py` and\n"
        "> `selftest.py`. Only analyse this artifact by hand if the ladder runs out, and say so\n"
        "> in the report.\n\n" % names)


def absence_is_safe(healths):
    """May the caller state a negative finding ('nothing matched') from these files?"""
    return bool(healths) and all(h.verdict == OK for h in healths)


def render_diagnose(healths, out, overlay_path=None, overlay=None):
    """--diagnose output: parse statistics and unparsed samples, no analysis."""
    w = out.write
    w("# Diagnose\n\n")
    if overlay is not None:
        w("%s\n\n" % overlay_note(overlay_path, overlay))
    if not healths:
        w("No files were read. Check the inventory and the --node filter.\n")
        return
    for h in healths:
        w("## `%s`  (%s)\n\n" % (h.name, h.kind or "unknown kind"))
        w("- lines read: %d\n- lines parsed: %d (%.1f%%)\n- content matches: %d\n- verdict: **%s**\n"
          % (h.lines, h.parsed, 100.0 * h.rate, h.recognised, h.verdict))
        if h.note:
            w("- note: %s\n" % h.note)
        w("\n")
        if h.samples:
            w("First %d lines that did not match the line grammar:\n\n```\n" % len(h.samples))
            for lineno, text in h.samples:
                w("%6s | %s\n" % (lineno if lineno is not None else "?", text))
            w("```\n\n")
            w("Write a regex covering these into `site-patterns.json`, then re-run\n"
              "`preflight.py` and `selftest.py`. See `references/90-when-scripts-fail.md`.\n\n")
        elif h.verdict == OK:
            w("Every line parsed. If the digest still looks thin, the LINE grammar is fine but\n"
              "the CONTENT patterns are not matching this site's messages - extend\n"
              "`ignite_events` / `os_patterns` in the overlay rather than the line layout.\n\n")


def write_template(path):
    """Write a commented starter overlay."""
    Path(path).write_text(json.dumps(TEMPLATE, indent=2) + "\n", encoding="utf-8")

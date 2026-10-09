#!/usr/bin/env python3
"""On-page SEO audit for HTML files, directories, or live URLs.

Uses only the Python standard library.

Examples:
    python3 scripts/seo_audit.py index.html
    python3 scripts/seo_audit.py foundations/            # every .html file under it
    python3 scripts/seo_audit.py https://example.com
    python3 scripts/seo_audit.py site/ --json > report.json
    python3 scripts/seo_audit.py site/ --fail-on error   # non-zero exit for CI

Exit code is 1 if any finding meets the --fail-on severity, else 0.
"""

import argparse
import json
import re
import sys
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

TITLE_RANGE = (30, 60)
DESCRIPTION_RANGE = (70, 160)
MIN_WORDS = 300
SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2}
VOID_SKIP_TEXT = {"script", "style", "noscript", "template"}


class PageParser(HTMLParser):
    """Collects the elements an SEO audit cares about in a single pass."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lang = None
        self.title = None
        self.metas = []
        self.links = []
        self.headings = []
        self.images = []
        self.anchors = []
        self.json_ld = 0
        self.text_parts = []
        self._in_title = False
        self._heading = None
        self._anchor = None
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.lang = a.get("lang")
        elif tag == "title":
            self._in_title = True
            self.title = ""
        elif tag == "meta":
            self.metas.append(a)
        elif tag == "link":
            self.links.append(a)
        elif tag == "img":
            self.images.append(a)
        elif tag == "a":
            self._anchor = {"href": a.get("href"), "rel": a.get("rel", ""), "text": ""}
        elif re.fullmatch(r"h[1-6]", tag):
            self._heading = [int(tag[1]), ""]
        if tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self.json_ld += 1
        if tag in VOID_SKIP_TEXT:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._anchor is not None:
            self.anchors.append(self._anchor)
            self._anchor = None
        elif self._heading and tag == f"h{self._heading[0]}":
            self.headings.append((self._heading[0], self._heading[1].strip()))
            self._heading = None
        if tag in VOID_SKIP_TEXT and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._in_title:
            self.title += data
            return
        if self._skip_depth:
            return
        self.text_parts.append(data)
        if self._heading:
            self._heading[1] += data
        if self._anchor is not None:
            self._anchor["text"] += data

    def meta(self, key):
        """Return the content of a meta tag matched by name or property."""
        key = key.lower()
        for m in self.metas:
            if m.get("name", "").lower() == key or m.get("property", "").lower() == key:
                return m.get("content", "").strip()
        return None

    def link_rel(self, rel):
        for link in self.links:
            if rel in link.get("rel", "").lower().split():
                return link.get("href")
        return None


def audit(html):
    """Return (findings, stats) for an HTML string."""
    p = PageParser()
    p.feed(html)
    findings = []

    def add(severity, check, message):
        findings.append({"severity": severity, "check": check, "message": message})

    # Document basics
    if not re.match(r"\s*<!doctype html", html, re.I):
        add("warning", "doctype", "Missing <!DOCTYPE html>.")
    if not p.lang:
        add("error", "lang", '<html> has no lang attribute (e.g. lang="en").')
    if not any(m.get("charset") for m in p.metas):
        add("warning", "charset", 'Missing <meta charset="utf-8">.')
    if p.meta("viewport") is None:
        add("error", "viewport", "Missing viewport meta tag; page may not be mobile-friendly.")

    # Title
    title = (p.title or "").strip()
    if not title:
        add("error", "title", "Missing or empty <title>.")
    elif not TITLE_RANGE[0] <= len(title) <= TITLE_RANGE[1]:
        add("warning", "title",
            f"Title is {len(title)} chars; aim for {TITLE_RANGE[0]}-{TITLE_RANGE[1]}: {title!r}")

    # Meta description
    desc = p.meta("description")
    if not desc:
        add("error", "description", "Missing meta description.")
    elif not DESCRIPTION_RANGE[0] <= len(desc) <= DESCRIPTION_RANGE[1]:
        add("warning", "description",
            f"Meta description is {len(desc)} chars; aim for "
            f"{DESCRIPTION_RANGE[0]}-{DESCRIPTION_RANGE[1]}.")

    # Robots / canonical
    robots = (p.meta("robots") or "").lower()
    if "noindex" in robots:
        add("warning", "robots", "Page is marked noindex and will not appear in search results.")
    if not p.link_rel("canonical"):
        add("info", "canonical", 'No <link rel="canonical"> tag.')

    # Headings
    h1s = [text for level, text in p.headings if level == 1]
    if not h1s:
        add("error", "h1", "No <h1> on the page.")
    elif len(h1s) > 1:
        add("warning", "h1", f"{len(h1s)} <h1> tags found; use one main heading.")
    if any(not text for _, text in p.headings):
        add("warning", "headings", "One or more headings are empty.")
    prev = 0
    for level, text in p.headings:
        if prev and level > prev + 1:
            add("warning", "headings", f"Heading level skips from h{prev} to h{level} at {text!r}.")
        prev = level

    # Images
    missing_alt = [img.get("src", "?") for img in p.images if "alt" not in img]
    if missing_alt:
        add("error", "img-alt",
            f"{len(missing_alt)} image(s) missing alt text: {', '.join(missing_alt[:5])}"
            + (" ..." if len(missing_alt) > 5 else ""))
    no_dims = [img for img in p.images if not (img.get("width") and img.get("height"))]
    if no_dims:
        add("info", "img-size",
            f"{len(no_dims)} image(s) lack width/height attributes (can cause layout shift).")

    # Links
    vague = {"click here", "here", "read more", "more", "link", "this"}
    vague_links = [a for a in p.anchors if a["text"].strip().lower() in vague]
    if vague_links:
        add("warning", "link-text", f"{len(vague_links)} link(s) use vague text like 'click here'.")
    empty_href = [a for a in p.anchors if not a["href"] or a["href"].strip() in ("#", "")]
    if empty_href:
        add("info", "links", f"{len(empty_href)} link(s) have an empty or '#' href.")

    # Social / structured data
    missing_og = [k for k in ("og:title", "og:description", "og:image") if not p.meta(k)]
    if missing_og:
        add("info", "open-graph", f"Missing Open Graph tags: {', '.join(missing_og)}.")
    if not p.meta("twitter:card"):
        add("info", "twitter", "Missing twitter:card meta tag.")
    if not p.json_ld:
        add("info", "structured-data", "No JSON-LD structured data found.")

    # Content
    words = len(re.findall(r"\w+", " ".join(p.text_parts)))
    if words < MIN_WORDS:
        add("info", "content", f"Only {words} words of visible text; thin content may rank poorly.")

    stats = {
        "title": title,
        "description": desc,
        "words": words,
        "headings": len(p.headings),
        "images": len(p.images),
        "links": len(p.anchors),
    }
    return findings, stats


def load(target):
    if re.match(r"https?://", target):
        req = urllib.request.Request(target, headers={"User-Agent": "seo-audit/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            charset = resp.headers.get_content_charset() or "utf-8"
            return resp.read().decode(charset, errors="replace")
    return Path(target).read_text(encoding="utf-8", errors="replace")


def expand(targets):
    for t in targets:
        path = Path(t)
        if not re.match(r"https?://", t) and path.is_dir():
            yield from (str(f) for f in sorted(path.rglob("*.htm*")))
        else:
            yield t


def score(findings):
    penalty = {"error": 15, "warning": 5, "info": 1}
    return max(0, 100 - sum(penalty[f["severity"]] for f in findings))


def print_report(results, min_severity):
    icons = {"error": "✖", "warning": "!", "info": "i"}
    for r in results:
        print(f"\n{r['target']}  —  score {r['score']}/100")
        if "error_loading" in r:
            print(f"  ✖ could not load: {r['error_loading']}")
            continue
        shown = [f for f in r["findings"]
                 if SEVERITY_ORDER[f["severity"]] >= SEVERITY_ORDER[min_severity]]
        shown.sort(key=lambda f: -SEVERITY_ORDER[f["severity"]])
        if not shown:
            print("  ✔ no issues")
        for f in shown:
            print(f"  {icons[f['severity']]} [{f['severity']}] {f['check']}: {f['message']}")
    if len(results) > 1:
        avg = sum(r["score"] for r in results) / len(results)
        print(f"\n{len(results)} page(s) audited, average score {avg:.0f}/100")


def main(argv=None):
    ap = argparse.ArgumentParser(description="On-page SEO audit for HTML files, folders, or URLs.")
    ap.add_argument("targets", nargs="+", help="HTML file, directory, or http(s) URL")
    ap.add_argument("--json", action="store_true", help="output machine-readable JSON")
    ap.add_argument("--min-severity", choices=SEVERITY_ORDER, default="info",
                    help="hide findings below this severity in the text report")
    ap.add_argument("--fail-on", choices=SEVERITY_ORDER, default="error",
                    help="exit 1 if any finding is at or above this severity")
    args = ap.parse_args(argv)

    results = []
    for target in expand(args.targets):
        try:
            findings, stats = audit(load(target))
            results.append({"target": target, "score": score(findings),
                            "stats": stats, "findings": findings})
        except Exception as exc:  # report and keep going
            results.append({"target": target, "score": 0, "error_loading": str(exc),
                            "findings": []})

    if args.json:
        json.dump(results, sys.stdout, indent=2, ensure_ascii=False)
        print()
    else:
        print_report(results, args.min_severity)

    threshold = SEVERITY_ORDER[args.fail_on]
    failed = any("error_loading" in r or
                 any(SEVERITY_ORDER[f["severity"]] >= threshold for f in r["findings"])
                 for r in results)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

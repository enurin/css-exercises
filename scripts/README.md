# SEO audit script

`seo_audit.py` checks HTML pages for common on-page SEO problems. It needs only Python 3, with no packages to install.

```sh
python3 scripts/seo_audit.py page.html               # one file
python3 scripts/seo_audit.py foundations/            # every .html file in a folder
python3 scripts/seo_audit.py https://your-site.com   # a live page
python3 scripts/seo_audit.py site/ --min-severity warning   # hide info-level notes
python3 scripts/seo_audit.py site/ --json > report.json     # machine-readable output
python3 scripts/seo_audit.py site/ --fail-on warning        # CI gate (exit 1 on warnings+)
```

## What it checks

| Severity | Checks |
|----------|--------|
| error    | missing `<title>`, meta description, `<h1>`, `lang`, viewport, image `alt` |
| warning  | title length (30–60), description length (70–160), multiple/empty/skipped headings, `noindex`, vague link text ("click here"), missing doctype/charset |
| info     | canonical link, Open Graph and Twitter card tags, JSON-LD structured data, image width/height, empty `#` links, thin content (< 300 words) |

Each page gets a score out of 100 (−15 per error, −5 per warning, −1 per info).

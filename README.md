# GSC Opportunity MCP

An MCP server that ranks Google Search Console opportunities by **estimated revenue**, not by clicks.

Most SEO tooling tells you what has the most traffic. This tells you what is worth fixing
first: it scores every opportunity as *projected incremental clicks x your revenue per 1000
clicks*, and returns one ranked list you can work top to bottom.

- **Read-only.** Scope is `webmasters.readonly`. It cannot change your site or your property.
- **Local.** Runs on your machine against your own credentials. Nothing is sent anywhere but Google.
- **Transparent.** The money figures are arithmetic you can audit, not a black-box score.
- **Headless-safe.** Works in cron and agents; no interactive re-auth on token expiry.

## What it gives you

- **Striking distance** — queries ranking 4-15, i.e. page two and the bottom of page one.
  These are the cheapest wins on any site.
- **Content decay** — pages that lost clicks versus the previous window, with the delta.
- **Cannibalization** — queries where two or more of your pages compete and split authority.
- **Revenue ranking across all three** — one list, sorted by dollars, not by category.

## Install

Requires Python 3.10+.

```bash
git clone https://github.com/<you>/gsc-opportunity-mcp.git
cd gsc-opportunity-mcp
python3 -m venv .venv
.venv/bin/pip install -e .
cp .env.example .env      # then edit .env
```

## Authentication

The server uses Google [Application Default Credentials](https://cloud.google.com/docs/authentication/application-default-credentials).
Two supported paths:

**(a) User credentials (easiest locally)**

```bash
gcloud auth application-default login \
  --scopes https://www.googleapis.com/auth/webmasters.readonly
```

**(b) Service account (best for servers and cron)**

Create a service account in a Google Cloud project with the *Search Console API* enabled,
download the JSON key, then:

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/key.json
```

**Either way, the credentials must have access to the property.** User credentials inherit
your own access. For a service account you must add its email address
(`something@project.iam.gserviceaccount.com`) as a **user** on the Search Console property
itself — Settings -> Users and permissions -> Add user. Read permission is enough.

## Configure

Edit `.env`:

```
CREATOR_SEO_SITE=sc-domain:example.com
CREATOR_SEO_SITE_RPM=5
CREATOR_SEO_AFFILIATE_CATEGORIES={}
```

Use the **domain property** form (`sc-domain:example.com`) where possible: it covers
subdomains and both http/https.

### About the numbers

Every dollar figure is an **estimate used for ranking**, and says so. Two inputs:

- `CREATOR_SEO_SITE_RPM` — your display-ad revenue per 1000 pageviews. Default `5` is a
  placeholder. Set it to your real RPM and the ranking becomes meaningful.
- `CREATOR_SEO_AFFILIATE_CATEGORIES` — a JSON map of query/page substring to extra
  dollars per 1000 clicks. `{"smart ring": 40}` means queries containing "smart ring"
  score $40/1k higher. This is how commercial-intent pages float to the top.

Projected clicks assume a row can reach a 10% CTR (`target_ctr`), which is optimistic for
low-intent queries. It is a consistent yardstick for *ordering* work, not a revenue forecast.

If you use [Raptive](https://raptive.com), set `RAPTIVE_CLIENT_ID` / `RAPTIVE_CLIENT_SECRET`
and the server uses **real per-page RPM** instead of the flat estimate. Without those
credentials it silently falls back to `CREATOR_SEO_SITE_RPM` — no Raptive account required.

## Wire it into an MCP client

```json
{
  "mcpServers": {
    "gsc-opportunity": {
      "command": "/absolute/path/to/.venv/bin/gsc-opportunity-mcp",
      "env": { "CREATOR_SEO_SITE": "sc-domain:example.com" }
    }
  }
}
```

## Tools

- `health_check` — auth and config status; never hangs on an expired token
- `list_sites` — properties the credentials can read
- `get_top_opportunities` — **the headline**: all three signals, ranked by estimated revenue
- `get_striking_distance_keywords` — queries at position 4-15 with gap to page 1
- `get_page_performance` — one URL: totals plus the queries driving it
- `analyze_content_decay` — pages losing clicks versus the prior window
- `find_cannibalization` — queries split across multiple pages
- `get_raptive_revenue` — real Raptive revenue, pageviews and blended RPM (optional)
- `get_raptive_page_performance` — per-page Raptive RPM and earnings (optional)

## Development

```bash
.venv/bin/pip install pytest
.venv/bin/pytest -q
```

Tests run without network access or credentials.

## Prior art

The revenue-weighted ranking idea is lifted from `Yoshyaes/creator-seo-mcp`, which requires
a separate OAuth client and a `credentials.json` that breaks on token expiry in headless
runs. This rebuilds the idea on plain ADC with a read-only scope.

## License

MIT.

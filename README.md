# Meta Ads MCP Server

A read-only Model Context Protocol (MCP) server for the Meta (Facebook) Marketing API, engineered for low token usage and unambiguous tool selection.

## Design Principles

**One insights tool.** Performance reporting at every aggregation level (account, campaign, ad set, ad) goes through a single `meta_ads_get_insights` tool with a `level` parameter. There are no separate campaign-performance or adset-performance tools, so there is nothing to confuse.

**Compact output by default.** Every tool accepts `format='compact'` (CSV rows plus terse key=value header lines, the default) or `format='json'` (minified, null fields stripped). Compact output avoids repeating JSON keys on every row and is typically a fraction of the size of an equivalent JSON payload.

**Automatic pagination.** All list and insights calls follow Meta's pagination cursors internally and return complete results. A page guard caps runaway requests and reports truncation explicitly.

**Resilient by default.** Transient failures (HTTP 5xx, 429, and Meta's rate limit error codes) are retried with backoff. Permanent errors surface as MCP tool errors with actionable guidance, such as which filter operators Meta actually accepts.

## Tools

| Tool | Purpose |
|------|---------|
| `meta_ads_list_accounts` | Discover ad accounts (the entry point) |
| `meta_ads_list_campaigns` | List campaigns with server-side filters |
| `meta_ads_list_adsets` | List ad sets; promoted_object flattened into pixel columns |
| `meta_ads_list_ads` | List ads with creative ids and public preview links |
| `meta_ads_get_creatives` | Batch-fetch ad copy for up to 50 creatives in one call |
| `meta_ads_get_insights` | Performance reporting at any level (the single reporting tool) |
| `meta_ads_list_pixels` | List pixels with freshness signals |
| `meta_ads_get_pixel_stats` | Pixel event volume by dimension, rolled up to day by default |
| `meta_ads_get_pixel_health` | Diagnostic checks plus dataset quality, non-PASS only |
| `meta_ads_list_custom_conversions` | List custom conversions with freshness signals |
| `meta_ads_get_custom_conversion_stats` | Fire volume for one custom conversion |

### Conventions shared by every tool

- `account_id` comes from `meta_ads_list_accounts`; the `act_` prefix is optional everywhere.
- Dates are `YYYY-MM-DD` and every date range is inclusive on both ends.
- `name_contains` filters are CASE-SENSITIVE server-side substring matches. Meta supports no other name operator, so anchor the substring for prefix-style matching.
- Status parameters default to `ACTIVE`; pass `ALL` to include paused and archived entities.
- Budget and spend values are in the account currency. Budget columns are converted from Meta's cent values.
- Output `status` columns carry Meta's `effective_status`.

### Insights specifics

- Id and name columns for the chosen `level` are added automatically.
- `actions` flatten to `action_<type>` columns. Registered custom conversions appear as `action_offsite_conversion.custom.<id>`.
- For unregistered pixel events an ad set optimizes on, request the `conversions` metric instead of `actions`. It matches the Ads Manager Results column, while `actions` over-counts.
- Compact output includes a `totals:` line over all rows (even truncated ones) with `ctr_pct` and `cpc` computed the way the UI does, from `inline_link_clicks`. The API's own `ctr`/`cpc` fields count all clicks.
- Results are capped at 1000 rows with an explicit `truncated:` line; list tools cap at 500 rows.

### Meta API quirks this server handles

- `name` filtering only accepts the `CONTAIN` operator (`STARTS_WITH` returns error 100).
- `objective` filtering only accepts `IN`. Legacy campaigns match their original stored objective (for example `LINK_CLICKS`), not the ODAX value they read back as, so the `objective` parameter accepts a list for union matching.
- Insights date ranges are sent as a `time_range` JSON object, which Meta respects reliably where bare `since`/`until` parameters do not.
- Pixel stats arrive hourly from Meta and are rolled up to daily buckets by default.
- `owner_ad_account` is excluded from pixel field lists because Meta rejects the entire request when the token cannot read the linked account.
- Dataset quality may require extra permissions; `meta_ads_get_pixel_health` degrades gracefully and reports the partial failure inline instead of erroring.

## Prerequisites

1. A Meta app with the Marketing API enabled ([Facebook Developers](https://developers.facebook.com/))
2. A long-lived access token with `ads_read` permission (plus `business_management` for pixel dataset quality)

## Installation

### Step 1: Python environment

```bash
cd /path/to/meta-ads-mcp-server
pyenv install 3.11.1
pyenv local 3.11.1
pyenv virtualenv 3.11.1 meta-ads-mcp
pyenv activate meta-ads-mcp
```

### Step 2: Dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 3: Environment variables

```bash
cp .env.example .env
```

Edit `.env`:

```bash
FACEBOOK_ACCESS_TOKEN=your_long_lived_token_here
```

The access token is the only required configuration. `FACEBOOK_API_VERSION` exists as an optional override for emergency pins or rollbacks; when it is unset the code's tested default (v26.0) applies.

Keep the `.env` file secure and never commit it to version control.

## Testing

Offline test suite (a fake Graph API stands in for Meta, no token needed):

```bash
python -m pytest tests/ -q
```

Run the server locally:

```bash
fastmcp dev server.py
# or
python server.py
```

## Deployment to FastMCP Cloud

```bash
fastmcp login
fastmcp deploy
fastmcp env set FACEBOOK_ACCESS_TOKEN="your_token_here"
```

The token is the only secret the deployment needs.

## Example Calls

Total account performance for a month:

```python
meta_ads_get_insights(
    account_id="123456",
    start_date="2026-01-01",
    end_date="2026-01-31",
)
```

Daily campaign performance (one row per campaign per day):

```python
meta_ads_get_insights(
    account_id="123456",
    start_date="2026-01-01",
    end_date="2026-01-31",
    level="campaign",
    time_increment="day",
)
```

Audit which pixel each active ad set fires against:

```python
meta_ads_list_adsets(account_id="123456", campaign_id="123...")
```

Pull the full ad copy for every creative in a campaign:

```python
ads = meta_ads_list_ads(account_id="123456", campaign_id="123...")
meta_ads_get_creatives(creative_ids=[...creative_id values from ads...])
```

## Ad Preview Links

`meta_ads_list_ads` returns a `preview_link` for each ad: a public fb.me URL that renders the complete ad (carousel slides included) without login.

Caveats:

- Links are temporary. Draft or unpublished ads expire after roughly 30 days.
- Each API call generates a new short URL for the same ad, so fetch fresh links instead of caching them.
- Links for policy-rejected ads stop working.
- Anyone with the link can view the preview, so share unapproved ads deliberately.

## Troubleshooting

**Token errors (code 190):** Long-lived tokens expire after about 60 days. Regenerate `FACEBOOK_ACCESS_TOKEN`.

**Empty results:** Status filters default to `ACTIVE`. Pass `status='ALL'` to see paused and archived entities. Verify the account id with `meta_ads_list_accounts`.

**Rate limits (codes 4, 17, 613, 80004):** The server retries with backoff automatically. If an error still surfaces, wait a few minutes; prefer `time_increment='total'` and entity id filters to shrink requests.

**Error 100 on filters:** Meta only accepts `CONTAIN` on name and `IN` on objective. The convenience parameters already compile to the valid operators; raw `extra_filters` must follow the same rules.

## Resources

- [Marketing API Documentation](https://developers.facebook.com/docs/marketing-apis)
- [Marketing API Insights](https://developers.facebook.com/docs/marketing-api/insights)
- [Graph API Reference](https://developers.facebook.com/docs/graph-api)
- [FastMCP Documentation](https://gofastmcp.com/)

## License

This project is provided as-is for use with the Meta Marketing API.

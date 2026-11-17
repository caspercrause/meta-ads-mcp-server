# Meta Ads MCP Server

Two Model Context Protocol (MCP) servers for the Meta (Facebook) Marketing API, engineered for low token usage and unambiguous tool selection:

- **Read server** (`server.py`) - strictly read-only reporting and diagnostics. This is the server most users connect.
- **Write server** (`write_server.py`) - a separately deployed server whose only capability is changing existing campaign or ad set budgets and end dates, behind a preview step, signed change tokens, and a write password known only to human operators. See [Write server](#write-server) below.

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
FACEBOOK_ACCESS_TOKEN_R=your_read_token_here
```

The read token is the only configuration the read server requires (the legacy `FACEBOOK_ACCESS_TOKEN` name still works as a fallback). The write server instead requires `FACEBOOK_ACCESS_TOKEN_W`, the `ads_management` token of a dedicated write system user, with no fallback. `FACEBOOK_API_VERSION` exists as an optional override for emergency pins or rollbacks; when it is unset the code's tested default (v26.0) applies.

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
fastmcp env set FACEBOOK_ACCESS_TOKEN_R="your_read_token_here"
```

The read token is the only secret the read deployment needs.

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

## Write server

`write_server.py` is a second, independently deployed MCP server. Its scope is deliberately tiny: change an **existing** daily budget, lifetime budget, or end date on a campaign or ad set. It never creates entities, never changes statuses, never switches an entity between daily and lifetime budgeting, and never adds a budget where none exists.

Every change is a two-step, human-in-the-loop flow:

1. `meta_ads_preview_changes` (read-only) diffs each proposed change against the live entity and returns a signed `change_token` per acceptable row. Tokens expire after 15 minutes and are bound to the exact old and new values. The preview shows the raw API value that would be sent, so the unit conversion is always visible.
2. A human reviews the diff and types their approval: a one-time code from their authenticator app when `META_WRITE_TOTP_SECRET` is configured (recommended; codes are single-use and expire within about a minute, so nothing durable ever sits in a conversation transcript), or the static `META_WRITE_PASSWORD` on servers without TOTP.
3. `meta_ads_apply_changes` accepts tokens only together with a valid approval code. Each entity is re-read first, and a row is refused as `stale` when its live value changed after the preview. After writing, the entity is read once more to verify the result.

Guardrails on top of the flow:

- Budgets are entered in **account currency units** (`515.50` means CHF 515.50 on a CHF account). The server converts to Meta's minor-unit convention internally, including the eleven no-cent currencies (JPY, KRW, TWD, HUF, and others) where raw values are whole units.
- Changes beyond a x5 factor of the current value, or above configurable caps, are blocked unless the preview is re-run with `override=true` after explicit human confirmation. A change that would set a budget to 1,000,000 currency units or more is refused outright, even with override. For the no-cent currencies (JPY, KRW, and the other offset-1 currencies), whose denominations run about 100x larger, every unit-denominated guard scales x100, making the hard ceiling 100,000,000 there.
- Budget changes on the wrong entity are blocked with a pointer to the right one: ad sets under a campaign-budget (CBO) campaign redirect to the campaign, and campaigns whose budgets sit on their ad sets (ABO) redirect to the ad sets.
- End dates are entered as `YYYY-MM-DD` (or `YYYY-MM-DD HH:MM`) in the account timezone and sent as ISO 8601 with an explicit UTC offset. Past dates are refused, lifetime-budget re-pacing is warned about, and changing a campaign's `stop_time` warns when its ad sets carry their own `end_time` values.
- Batches are capped at 50 changes so every batch stays reviewable, and apply rows are independent: one failure never blocks the rest.

### Operator enrollment

`totp_enroll.py` produces the authenticator QR codes. Run it locally in a plain terminal, never inside an AI conversation, because the QR visually encodes the secret. Operators never need Python themselves; the administrator runs the commands and operators scan the QR off the administrator's screen.

| Goal | Command |
|------|---------|
| Enroll a NEW operator (fresh secret) | `python totp_enroll.py --name alice` |
| Re-render an existing secret (replacement phone, second phone) | `python totp_enroll.py --name alice --secret BASE32...` |
| Re-render the secret stored in the local `.env` | `python totp_enroll.py --from-env` |

`--name` only sets the label shown in the authenticator app. The secret is reused whenever `--secret` or `--from-env` is given, and freshly generated only when both are absent, so every phone that scans a `--from-env` QR shares one identical secret.

Server-side configuration accepts both forms, together or alone:

- `META_WRITE_TOTP_SECRET=BASE32...` - one shared secret; every approval logs as `approved_by=operator`.
- `META_WRITE_TOTP_SECRETS=alice:BASE32ONE,bob:BASE32TWO` - per-operator pairs. On each approval attempt the server tries the submitted code against every configured secret; the first match identifies the operator, whose name is recorded in the tool output and the server log as `approved_by=<name>`.

Codes are single-use (a replay cache refuses a code that already approved a batch, per operator) and failed attempts are logged and throttled by one second. Onboarding is appending `,name:SECRET` to the list and offboarding is deleting that entry; locally the change applies on the next call since the env is read per request, while a deployed service needs a restart to pick up new env values. Secrets travel between people only via a password manager, never chat or email, since holding a secret means holding approval power.

**Trade-offs of the shared-secret form:** no per-person attribution, revocation is all-or-nothing, and because all phones show the same code simultaneously, two operators approving within the same roughly 90-second window collide (the second is refused as already used). Per-operator pairs avoid all three.

Deploy it as its own service with `write_server.py` as the entrypoint and two secrets: `FACEBOOK_ACCESS_TOKEN_W` (the write system user's `ads_management` token; only the ad accounts assigned to that system user can ever be changed) plus `META_WRITE_TOTP_SECRET` (recommended) or `META_WRITE_PASSWORD`. Generate the TOTP secret outside any AI conversation (`python -c "import pyotp; print(pyotp.random_base32())"`) and enter the same value in the operator's authenticator app. Without a configured approval secret, applying is disabled and previews become dry runs.

## Troubleshooting

**Token errors (code 190):** Long-lived tokens expire after about 60 days. Regenerate the affected token (`FACEBOOK_ACCESS_TOKEN_R` or `FACEBOOK_ACCESS_TOKEN_W`).

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

"""
Meta (Facebook) Ads WRITE MCP server - budget and end-date changes only.

Deliberately a separate server from the read-only reporting MCP, so the
ability to change client accounts exists only where this server is
explicitly connected (two-server architecture). Scope is intentionally
tiny: change an EXISTING daily budget, lifetime budget or end date on a
campaign or ad set. It never creates anything, never changes statuses,
and never switches an entity between daily and lifetime budgeting.

Human-in-the-loop contract (enforced server-side):
1. meta_ads_preview_changes (read-only) diffs every proposed change
   against the live entity and returns a signed change_token per
   acceptable row. Tokens are bound to the exact old and new values and
   expire after 15 minutes.
2. A human reviews the diff and types the write password
   (META_WRITE_PASSWORD, configured only in the server environment).
3. meta_ads_apply_changes accepts tokens only with the correct password,
   re-reads each entity first and refuses any row whose live value
   changed after the preview, then re-reads again to verify the result.

Units: budgets are entered in account currency units (CHF 515.50 ->
515.5). Meta's raw API values are minor units: x100 for most currencies,
x1 for the eleven no-cent currencies (JPY, KRW, TWD, HUF, ...). The raw
value that will be sent is echoed in every preview row, so the
conversion is always visible before anything is applied.
"""
import base64
import hashlib
import hmac
import json
import logging
import math
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple, Union
from zoneinfo import ZoneInfo

import pyotp
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel

from facebook_client import FacebookAdsClient, FacebookAPIError
from rendering import SEP, Format, Id, _csv, _error_message, _json, _kv, _lines

INSTRUCTIONS = """Meta (Facebook) Marketing API WRITE server - budget and end-date changes only.
Every change is two-step: meta_ads_preview_changes returns a diff for the human to review
plus a signed change_token per row (valid 15 minutes); meta_ads_apply_changes applies
tokens only together with an approval_code typed by the human operator: a one-time code
from their authenticator app (single-use; a typed code stays valid for roughly 90
seconds), or the static write password on servers without TOTP. ALWAYS ask the human for
a fresh code per batch; never guess one or reuse one from earlier context. Budgets are
entered in ACCOUNT CURRENCY UNITS (515.50 means CHF 515.50 on a CHF account); the server
converts to Meta's minor units itself and shows the raw api_value in the preview.
end_date values are 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM' in the account timezone (defaults
to 23:59). END DATES BELONG ON AD SETS: a campaign's stop_time is DERIVED from its ad
sets' end_times, and when every ad set carries its own end date Meta silently ignores
campaign-level writes, so the preview blocks them and lists the ad set ids to change
instead. To extend a campaign, list its ad sets on the read MCP server and preview an
end_date change per ad set; only target the campaign itself when its ad sets have no own
end dates. The server only
changes values that already exist (never daily<->lifetime switches, never adding a budget
or a first end date), and blocks changes bigger than a configured factor or cap unless
re-previewed with override=true. Apply re-reads every entity and refuses rows whose live
value changed since the preview. Get entity ids from the read-only Meta MCP server."""

mcp = FastMCP("meta_ads_write", instructions=INSTRUCTIONS, include_fastmcp_meta=False)

# The audit trail must exist regardless of root logging config: give this
# logger its own stderr handler at INFO (Python's last-resort handler would
# silently drop INFO lines, and the approved_by trail is a design promise).
logger = logging.getLogger("meta_ads_write")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(
        logging.Formatter("%(asctime)s %(name)s %(levelname)s %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False

# The MCP plumbing (mcp.server.*) logs every request and session teardown at
# INFO, burying the audit trail in deployment logs; keep the library at WARNING.
logging.getLogger("mcp").setLevel(logging.WARNING)

TOKEN_TTL_SECONDS = 900        # preview tokens die after 15 minutes
MAX_CHANGES_PER_CALL = 50      # keeps every batch humanly reviewable
HARD_CEILING_UNITS = 1_000_000  # absolute refusal, override or not
AUTH_FAILURE_DELAY_SECONDS = 1.0  # slows brute-force guessing of codes/passwords
# TOTP steps accepted around "now" (30s each). 2 keeps a typed code valid for
# 60-90s after it appears on the phone: the human-in-the-loop round trip
# (human types the code, the model composes the call, the client shows an
# approval dialog) regularly outlives the 30-60s a 1-step window allows.
# The single-use replay cache and the failure throttle keep this safe.
TOTP_VALID_WINDOW = 2


def _env_num(name: str, default: float, minimum: float) -> float:
    """Env override that falls back to the default on junk or unsafe values."""
    try:
        value = float(os.getenv(name, '') or default)
    except ValueError:
        return default
    return value if math.isfinite(value) and value >= minimum else default


MAX_CHANGE_FACTOR = _env_num('META_WRITE_MAX_FACTOR', 5.0, 1.0)
MAX_DAILY_UNITS = _env_num('META_WRITE_MAX_DAILY_BUDGET', 10_000.0, 1.0)
MAX_LIFETIME_UNITS = _env_num('META_WRITE_MAX_LIFETIME_BUDGET', 100_000.0, 1.0)

# Meta's offset-1 currencies: raw API budget values are whole units, not
# hundredths (developers.facebook.com/docs/marketing-api/currencies).
# Every other supported currency (CHF, EUR, USD, GBP, CAD, HKD, ...) is
# offset 100: raw values are cents. Verified live: raw 51500 on a CHF
# account renders as 515.00 in Ads Manager.
OFFSET_ONE = frozenset({'CLP', 'COP', 'CRC', 'HUF', 'ISK', 'IDR',
                        'JPY', 'KRW', 'PYG', 'TWD', 'VND'})

BudgetField = Literal['daily_budget', 'lifetime_budget']
ChangeField = Literal['daily_budget', 'lifetime_budget', 'end_date']

_CAMPAIGN_FIELDS = ['id', 'name', 'effective_status', 'daily_budget',
                    'lifetime_budget', 'stop_time', 'start_time', 'account_id']
_ADSET_FIELDS = ['id', 'name', 'effective_status', 'daily_budget',
                 'lifetime_budget', 'end_time', 'start_time', 'account_id',
                 'campaign_id']
# The nested campaign expansion is needed only by preview's CBO redirect
# check; apply's re-reads stay lean so a parent the token cannot expand
# can never fail an otherwise applicable write.
_ADSET_FIELDS_EXPANDED = _ADSET_FIELDS + ['campaign{id,daily_budget,lifetime_budget}']

_DEAD_STATUSES = {'ARCHIVED', 'DELETED'}

_client: Optional[FacebookAdsClient] = None


def _get_client() -> FacebookAdsClient:
    global _client
    if _client is None:
        # Deliberately no fallback to the generic/read token names: a write
        # deployment without its own ads_management token must fail fast.
        _client = FacebookAdsClient(token_env_vars=('FACEBOOK_ACCESS_TOKEN_W',))
    return _client


class Change(BaseModel):
    """One proposed change for meta_ads_preview_changes."""
    entity_type: Literal['campaign', 'adset']
    entity_id: Id
    field: ChangeField
    new_value: Union[float, str]
    override: bool = False


# ============================================================ units

def _offset(currency: Optional[str]) -> int:
    return 1 if (currency or '').upper() in OFFSET_ONE else 100


def _to_minor(units: float, currency: str) -> int:
    """Convert account-currency units to Meta's raw minor-unit integer."""
    if not math.isfinite(units) or units <= 0:
        raise ValueError(f"budget must be a positive number, got {units!r}")
    offset = _offset(currency)
    exact = units * offset
    rounded = round(exact)
    if abs(exact - rounded) > 1e-6:
        places = 2 if offset == 100 else 0
        raise ValueError(
            f"{currency} budgets support at most {places} decimal places, got {units!r}")
    return int(rounded)


def _units_str(minor: Any, currency: str) -> str:
    """Render a raw minor-unit value as account-currency units."""
    try:
        n = int(minor)
    except (TypeError, ValueError):
        return str(minor)
    if _offset(currency) == 100:
        return f"{n / 100:.2f}"
    return str(n)


# ============================================================ dates

def _account_tz(tzname: Optional[str]) -> Tuple[Any, Optional[str]]:
    try:
        return ZoneInfo(tzname), None
    except Exception:
        return timezone.utc, f"unknown account timezone {tzname!r}; interpreting the date as UTC"


def _parse_end_date(value: str, tz: Any) -> datetime:
    s = str(value).strip().replace('T', ' ')
    has_time = len(s) > 10
    try:
        dt = datetime.strptime(s, '%Y-%m-%d %H:%M' if has_time else '%Y-%m-%d')
    except ValueError:
        raise ValueError(
            f"end_date must be 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM', got {value!r}")
    if not has_time:
        dt = dt.replace(hour=23, minute=59)
    return dt.replace(tzinfo=tz)


def _parse_meta_time(s: Optional[str]) -> Optional[datetime]:
    """Parse Meta's ISO timestamps ('2026-11-22T23:00:00+0100')."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        try:
            return datetime.strptime(s, '%Y-%m-%dT%H:%M:%S%z')
        except ValueError:
            return None


def _same_instant(a: Optional[str], b: Optional[str]) -> bool:
    da, db = _parse_meta_time(a), _parse_meta_time(b)
    if da is None or db is None:
        return (a or None) == (b or None)
    return da == db


# ============================================================ tokens

def _signing_key() -> bytes:
    material = (os.getenv('FACEBOOK_ACCESS_TOKEN_W', '') + '|'
                + os.getenv('META_WRITE_PASSWORD', '') + '|'
                + os.getenv('META_WRITE_TOTP_SECRET', '') + '|'
                + os.getenv('META_WRITE_TOTP_SECRETS', ''))
    return hashlib.sha256(b'meta-write-v1|' + material.encode()).digest()


def _make_token(payload: Dict[str, Any]) -> str:
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(',', ':')).encode()).decode().rstrip('=')
    sig = hmac.new(_signing_key(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def _read_token(token: str) -> Dict[str, Any]:
    try:
        body, sig = token.rsplit('.', 1)
        expect = hmac.new(_signing_key(), body.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expect):
            raise ValueError
        payload = json.loads(base64.urlsafe_b64decode(body + '=' * (-len(body) % 4)))
    except (ValueError, TypeError, json.JSONDecodeError):
        raise ValueError("invalid change_token (not produced by meta_ads_preview_changes)")
    if payload.get('v') != 1:
        raise ValueError("change_token has an unknown schema version; "
                         "re-run meta_ads_preview_changes")
    if time.time() - float(payload.get('iat', 0)) > TOKEN_TTL_SECONDS:
        raise ValueError("change_token expired (older than 15 minutes); "
                         "re-run meta_ads_preview_changes")
    return payload


# ============================================================ preview

def _date_param(entity_type: str) -> str:
    return 'stop_time' if entity_type == 'campaign' else 'end_time'


def _fetch_entity(client: FacebookAdsClient, change_type: str, entity_id: str,
                  expand: bool = False) -> Dict[str, Any]:
    if change_type == 'campaign':
        fields = _CAMPAIGN_FIELDS
    else:
        fields = _ADSET_FIELDS_EXPANDED if expand else _ADSET_FIELDS
    return client.get_object(entity_id, fields)


def _account_info(client: FacebookAdsClient, account_id: str,
                  cache: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    key = account_id.removeprefix('act_')
    if key not in cache:
        cache[key] = client.get_object(f"act_{key}", ['currency', 'timezone_name'])
    return cache[key]


def _preview_budget(change: Change, entity: Dict[str, Any], currency: str,
                    row: Dict[str, Any], warnings: List[str]) -> Optional[Dict[str, Any]]:
    """Validate a budget change; fill row; return a token payload or None."""
    field = change.field
    other = 'lifetime_budget' if field == 'daily_budget' else 'daily_budget'
    raw_old = entity.get(field)
    row['current'] = _units_str(raw_old, currency) if raw_old is not None else ''

    if raw_old is None:
        if entity.get(other) is not None:
            row['result'] = (f"blocked: uses a {other.replace('_', ' ')} "
                             f"({currency} {_units_str(entity[other], currency)}); refusing to "
                             f"set {field} - that would switch the pacing model. "
                             f"Change {other} instead.")
        elif change.entity_type == 'adset' and any(
                (entity.get('campaign') or {}).get(k) is not None
                for k in ('daily_budget', 'lifetime_budget')):
            cid = (entity.get('campaign') or {}).get('id')
            row['result'] = (f"blocked: budget is managed by its campaign {cid} "
                             "(Advantage campaign budget); change the campaign instead")
        elif change.entity_type == 'campaign':
            row['result'] = ("blocked: no campaign-level budget - budgets sit on its "
                             "ad sets; list them with the read MCP and change those")
        else:
            row['result'] = f"blocked: ad set has no {field} set; this tool never adds one"
        return None

    try:
        units_new = float(change.new_value)
        minor_new = _to_minor(units_new, currency)
    except (TypeError, ValueError) as e:
        row['result'] = f"blocked: {e}"
        return None

    minor_old = int(raw_old)
    if minor_old <= 0:
        row['result'] = f"blocked: current {field} is {raw_old!r}; refusing to change it"
        return None
    row['proposed'] = _units_str(minor_new, currency)
    row['api_value'] = str(minor_new)
    if minor_new == minor_old:
        row['result'] = 'no_change: already at this value'
        return None

    factor = minor_new / minor_old
    row['change'] = f"{(factor - 1) * 100:+.0f}%"
    units_val = minor_new / _offset(currency)
    # Offset-1 currencies carry roughly 100x denominations (KRW, JPY, CLP...),
    # so unit-denominated guards scale up by 100 to stay meaningful there.
    guard_scale = 100 if _offset(currency) == 1 else 1
    cap = (MAX_DAILY_UNITS if field == 'daily_budget' else MAX_LIFETIME_UNITS) * guard_scale
    ceiling = HARD_CEILING_UNITS * guard_scale

    if units_val >= ceiling:
        row['result'] = (f"blocked: {currency} {units_val:,.0f} is at or above the hard "
                         f"ceiling of {ceiling:,.0f} and is never applied (typo guard)")
        return None
    if factor > MAX_CHANGE_FACTOR or factor < 1 / MAX_CHANGE_FACTOR:
        if not change.override:
            row['result'] = (f"blocked: x{factor:.2f} the current budget, outside the "
                             f"x{MAX_CHANGE_FACTOR:g} safety guard. If the human really "
                             "wants this, re-run preview with override=true")
            return None
        warnings.append(f"override accepted: x{factor:.2f} the current budget")
    if units_val > cap:
        if not change.override:
            row['result'] = (f"blocked: above the {cap:g} {currency} cap for {field}; "
                             "re-run preview with override=true after the human confirms")
            return None
        warnings.append(f"override accepted: above the {cap:g} {currency} cap")
    if 2 <= factor or factor <= 0.5:
        warnings.append(f"large change: x{factor:.2f}")

    row['result'] = 'ready'
    return {'old': str(raw_old), 'new': str(minor_new)}


def _preview_end_date(change: Change, entity: Dict[str, Any], currency: str,
                      tzname: Optional[str], client: FacebookAdsClient,
                      row: Dict[str, Any], warnings: List[str]) -> Optional[Dict[str, Any]]:
    """Validate an end-date change; fill row; return a token payload or None."""
    param = _date_param(change.entity_type)
    raw_old = entity.get(param)
    old_dt = _parse_meta_time(raw_old)
    row['current'] = old_dt.isoformat() if old_dt else '(none)'

    if old_dt is None:
        if raw_old:
            row['current'] = str(raw_old)
            row['result'] = (f"blocked: could not parse the existing {param} "
                             f"{raw_old!r}; refusing to change it blindly")
        else:
            row['result'] = (f"blocked: no existing {param} on this "
                             f"{change.entity_type}; this tool only changes existing "
                             "end dates, it does not add one (that would silently "
                             "give an evergreen run a stop)")
        return None

    tz, tz_warning = _account_tz(tzname)
    if tz_warning:
        warnings.append(tz_warning)
    try:
        new_dt = _parse_end_date(str(change.new_value), tz)
    except ValueError as e:
        row['result'] = f"blocked: {e}"
        return None

    new_iso = new_dt.isoformat()
    row['proposed'] = new_iso
    row['api_value'] = new_iso
    now = datetime.now(timezone.utc)

    if new_dt <= now:
        row['result'] = "blocked: end date is in the past"
        return None
    start_dt = _parse_meta_time(entity.get('start_time'))
    if start_dt and new_dt <= start_dt:
        row['result'] = f"blocked: end date is before the start time ({start_dt.isoformat()})"
        return None
    if abs((new_dt - old_dt).total_seconds()) < 60:
        row['result'] = 'no_change: already ends at this time'
        return None

    delta_days = (new_dt - old_dt).total_seconds() / 86400
    row['change'] = f"{delta_days:+.1f}d"
    if new_dt < old_dt:
        warnings.append(f"shortens the run by {-delta_days:.1f} days")
    if (new_dt - now).days > 730:
        warnings.append("end date is more than 2 years out - check the year")
    if entity.get('lifetime_budget') is not None:
        warnings.append(
            f"lifetime budget {currency} {_units_str(entity['lifetime_budget'], currency)} "
            "re-paces across the new window")
    if change.entity_type == 'campaign':
        try:
            # All statuses: paused ad sets schedule-drive the campaign too.
            adsets = client.get_ad_sets(
                entity.get('account_id', ''), campaign_id=str(entity['id']),
                extra_fields=['end_time'],
            ).get('data', [])
            own_ends = sorted(
                ((str(a.get('id')), dt) for a in adsets
                 if (dt := _parse_meta_time(a.get('end_time')))),
                key=lambda pair: pair[1])
            if own_ends and len(own_ends) == len(adsets):
                # Verified live: in this shape Meta accepts the stop_time
                # write with success:true and changes nothing, so redirect
                # instead of letting an apply end in verify_failed.
                shown = ' '.join(i for i, _ in own_ends[:10])
                more = f" (+{len(own_ends) - 10} more)" if len(own_ends) > 10 else ''
                row['result'] = (
                    f"blocked: all {len(own_ends)} ad sets carry their own "
                    f"end_time (earliest {own_ends[0][1].isoformat()[:16]}), so "
                    "this campaign's stop_time is derived from them and Meta "
                    "silently ignores direct writes to it. Change end_date on "
                    f"the ad sets instead: {shown}{more}")
                return None
            if own_ends:
                warnings.append(
                    f"{len(own_ends)} of {len(adsets)} ad sets have their own "
                    f"end_time (earliest {own_ends[0][1].isoformat()[:16]}); "
                    "campaign stop_time does not move those - change their "
                    "end_date too if they should follow")
        except Exception as e:  # advisory only - never block on a failed check
            warnings.append(f"could not check ad set end times: {_error_message(e)}")

    row['result'] = 'ready'
    return {'old': raw_old or None, 'new': new_iso}


@mcp.tool(name="meta_ads_preview_changes",
          annotations={"title": "Preview Meta Budget / End-Date Changes (read-only)",
                       "readOnlyHint": True, "destructiveHint": False,
                       "idempotentHint": True, "openWorldHint": True},
          output_schema=None)
def meta_ads_preview_changes(
    changes: List[Change],
    format: Format = "compact",
) -> str:
    """Diff proposed budget / end-date changes against live values. Step 1 of 2.

    Args:
        changes: Up to 50 of {entity_type: 'campaign'|'adset', entity_id,
            field: 'daily_budget'|'lifetime_budget'|'end_date', new_value,
            override}. Budgets in ACCOUNT CURRENCY UNITS (515.5 = CHF
            515.50), never cents; end_date 'YYYY-MM-DD' or 'YYYY-MM-DD
            HH:MM' in the account timezone (defaults to 23:59). End-date
            changes target the AD SETS: campaign stop_time is derived
            from ad set end_times, so campaign-level attempts are
            blocked with the ad set ids to use instead.
        format: 'compact' (CSV, default) or 'json'.

    Returns:
        Summary counts, then one row per change: current, proposed,
        api_value (the raw value that would be sent), change, result
        (ready / blocked / no_change), warnings, change_token (valid 15
        minutes, bound to the exact old->new values).
    """
    try:
        if not changes:
            raise ValueError("changes must contain at least one item")
        if len(changes) > MAX_CHANGES_PER_CALL:
            raise ValueError(f"changes is capped at {MAX_CHANGES_PER_CALL} per call; "
                             "split the batch")
        seen = set()
        for c in changes:
            key = (c.entity_id, c.field)
            if key in seen:
                raise ValueError(f"duplicate change for {c.entity_type} {c.entity_id} "
                                 f"field {c.field}; one change per entity+field per batch")
            seen.add(key)

        client = _get_client()
        accounts: Dict[str, Dict[str, Any]] = {}
        rows: List[Dict[str, Any]] = []
        counts = {'ready': 0, 'blocked': 0, 'no_change': 0}
        # Without a configured password nothing can ever be applied, and
        # configuring it later changes the signing key (invalidating any
        # token minted now), so the preview degrades to a dry run.
        can_apply = bool(_totp_secrets() or os.getenv('META_WRITE_PASSWORD'))

        for change in changes:
            row: Dict[str, Any] = {
                'entity': change.entity_type, 'id': str(change.entity_id),
                'name': None, 'status': None, 'field': change.field,
                'currency': None, 'current': None, 'proposed': None,
                'api_value': None, 'change': None, 'result': None,
                'warnings': None, 'change_token': None,
            }
            warnings: List[str] = []
            payload_core: Optional[Dict[str, Any]] = None
            try:
                entity = _fetch_entity(client, change.entity_type,
                                       str(change.entity_id), expand=True)
                row['name'] = entity.get('name')
                status = entity.get('effective_status')
                row['status'] = status
                account = _account_info(client, str(entity.get('account_id', '')), accounts)
                currency = account.get('currency') or ''
                row['currency'] = currency

                if status in _DEAD_STATUSES:
                    row['result'] = f"blocked: entity is {status} and cannot be changed"
                else:
                    if status and status != 'ACTIVE':
                        warnings.append(f"entity is {status}")
                    if change.field == 'end_date':
                        payload_core = _preview_end_date(
                            change, entity, currency, account.get('timezone_name'),
                            client, row, warnings)
                    else:
                        payload_core = _preview_budget(
                            change, entity, currency, row, warnings)
            except FacebookAPIError as e:
                row['result'] = f"blocked: {_error_message(e)}"
            except Exception as e:  # one bad row must never kill the batch
                row['result'] = f"blocked: unexpected error: {e}"

            if payload_core is not None:
                if can_apply:
                    payload = {'v': 1, 't': change.entity_type,
                               'id': str(change.entity_id), 'f': change.field,
                               'cur': row['currency'], 'iat': int(time.time()),
                               **payload_core}
                    row['change_token'] = _make_token(payload)
                counts['ready'] += 1
            elif str(row['result'] or '').startswith('no_change'):
                counts['no_change'] += 1
            else:
                counts['blocked'] += 1
            row['warnings'] = SEP.join(warnings) if warnings else None
            rows.append(row)

        header = dict(counts)
        header['token_ttl'] = f"{TOKEN_TTL_SECONDS // 60}min"
        if not can_apply:
            note = ("apply disabled: no approval secret (META_WRITE_TOTP_SECRET(S) "
                    "or META_WRITE_PASSWORD) is configured in the server environment, "
                    "so no change_tokens were issued; this preview is a dry run only")
        elif counts['ready']:
            ask = ("the current one-time code from the human's authenticator app"
                   if _totp_secrets() else "the write password typed by the human")
            note = ("apply: show this diff to the human, then call "
                    f"meta_ads_apply_changes with the change_token values and {ask}")
        else:
            note = None
        logger.info("preview n=%d ready=%d blocked=%d no_change=%d accounts=%s",
                    len(changes), counts['ready'], counts['blocked'],
                    counts['no_change'], ','.join(sorted(accounts)) or '-')
        if format == "json":
            out: Dict[str, Any] = {**header}
            if note:
                out['note'] = note
            out['rows'] = rows
            return _json(out)
        keep = ['entity', 'id', 'name', 'status', 'field', 'currency', 'current',
                'proposed', 'api_value', 'change', 'result', 'warnings', 'change_token']
        return _lines(_kv(header), note, _csv(rows, keep=keep))
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(_error_message(e)) from e


# ============================================================ apply

# One-time codes already used, kept briefly so a code can never approve two
# batches even inside its validity window (per-process; a multi-instance
# deployment should keep instances at 1 or accept the residual window).
_used_codes: Dict[str, float] = {}
USED_CODE_TTL_SECONDS = 300


def _totp_secrets() -> List[Tuple[str, str]]:
    """(operator_name, base32_secret) pairs from the environment.

    META_WRITE_TOTP_SECRET holds a single unnamed secret ('operator');
    META_WRITE_TOTP_SECRETS holds per-operator pairs 'name:SECRET,name:SECRET'
    so each approval is attributed and operators can be revoked individually.
    """
    pairs: List[Tuple[str, str]] = []
    single = os.getenv('META_WRITE_TOTP_SECRET')
    if single:
        pairs.append(('operator', single.strip()))
    for entry in (os.getenv('META_WRITE_TOTP_SECRETS') or '').split(','):
        name, _, secret = entry.strip().partition(':')
        if name and secret:
            pairs.append((name.strip(), secret.strip()))
    return pairs


def _auth_failure(message: str) -> ToolError:
    """Log, throttle, and build the error for a rejected approval attempt."""
    logger.warning("approval rejected: %s", message.split(' - ')[0])
    time.sleep(AUTH_FAILURE_DELAY_SECONDS)
    return ToolError(message)


def _check_auth(approval_code: str) -> Optional[str]:
    """Validate the human's approval: TOTP when configured, else password.

    Returns the approving operator's name for TOTP, None for password.
    """
    secrets_list = _totp_secrets()
    password = os.getenv('META_WRITE_PASSWORD') or ''
    if secrets_list:
        code = (approval_code or '').strip().replace(' ', '')
        now = time.time()
        for used, t in list(_used_codes.items()):
            if now - t > USED_CODE_TTL_SECONDS:
                del _used_codes[used]
        matched: Optional[str] = None
        usable = 0
        for name, secret in secrets_list:
            try:
                totp = pyotp.TOTP(secret)
                usable += 1
                if totp.verify(code, valid_window=TOTP_VALID_WINDOW):
                    matched = name
                    break
            except Exception:
                continue  # one malformed secret entry must not break the others
        if usable == 0:
            raise ToolError(
                "TOTP misconfigured: no configured secret is valid base32. "
                "Check META_WRITE_TOTP_SECRET / META_WRITE_TOTP_SECRETS in the "
                "server environment (pairs belong in the plural variable as "
                "'name:SECRET,name:SECRET'). Nothing was applied.")
        if matched is None:
            raise _auth_failure("invalid one-time code - ask the human operator for "
                                "the current code from their authenticator app. "
                                "Nothing was applied.")
        replay_key = f"{matched}:{code}"
        if replay_key in _used_codes:
            raise _auth_failure("this one-time code was already used - ask the human "
                                "for a fresh code. Nothing was applied.")
        _used_codes[replay_key] = now
        return matched
    if not password:
        raise ToolError("applying changes is disabled: neither META_WRITE_TOTP_SECRET(S) "
                        "nor META_WRITE_PASSWORD is configured in the server environment")
    if not approval_code or not hmac.compare_digest(approval_code.encode(),
                                                    password.encode()):
        raise _auth_failure("incorrect write password - ask the human operator to "
                            "type it. Nothing was applied.")
    return None


@mcp.tool(name="meta_ads_apply_changes",
          annotations={"title": "Apply Meta Budget / End-Date Changes (writes!)",
                       "readOnlyHint": False, "destructiveHint": True,
                       "idempotentHint": True, "openWorldHint": True},
          output_schema=None)
def meta_ads_apply_changes(
    change_tokens: List[str],
    approval_code: str,
    format: Format = "compact",
) -> str:
    """Apply previously previewed changes - this WRITES to Meta. Step 2 of 2.

    Args:
        change_tokens: change_token values from meta_ads_preview_changes.
        approval_code: The one-time code from the human operator's
            authenticator app (or the static write password where TOTP is
            not configured), typed by the human for this batch - ask them;
            never guess or reuse one.
        format: 'compact' (CSV, default) or 'json'.

    Returns:
        Summary counts, then one row per token: previous, applied, status
        (applied / already_applied / stale / invalid / error /
        verify_failed), detail. Rows are independent; stale rows (live
        value changed since preview) need a fresh preview.
    """
    try:
        # Cheap batch validation runs BEFORE auth so a rejected call never
        # burns the operator's single-use code.
        if not change_tokens:
            raise ValueError("change_tokens must contain at least one token")
        if len(change_tokens) > MAX_CHANGES_PER_CALL:
            raise ValueError(f"change_tokens is capped at {MAX_CHANGES_PER_CALL} per call")
        approver = _check_auth(approval_code)
        tokens = list(dict.fromkeys(change_tokens))
        # One short id per approved batch: every log line of this call carries
        # it, and it is echoed in the output header so a batch can be found in
        # the deployment logs from the conversation (and vice versa).
        batch_id = uuid.uuid4().hex[:8]
        logger.info("apply batch=%s tokens=%d approved_by=%s",
                    batch_id, len(tokens), approver or 'password')

        client = _get_client()
        rows: List[Dict[str, Any]] = []
        counts: Dict[str, int] = {}

        for token in tokens:
            row: Dict[str, Any] = {'entity': None, 'id': None, 'name': None,
                                   'field': None, 'previous': None, 'applied': None,
                                   'status': None, 'detail': None}
            try:
                payload = _read_token(token)
            except ValueError as e:
                row['status'] = 'invalid'
                row['detail'] = str(e)
                rows.append(row)
                counts['invalid'] = counts.get('invalid', 0) + 1
                continue

            try:
                etype, eid, field = payload['t'], payload['id'], payload['f']
                currency = payload.get('cur') or ''
                old, new = payload.get('old'), payload['new']
                param = field if field != 'end_date' else _date_param(etype)
                row.update({'entity': etype, 'id': eid, 'field': field})

                def _fmt(raw: Optional[str]) -> Optional[str]:
                    if raw is None:
                        return '(none)'
                    return raw if field == 'end_date' else _units_str(raw, currency)

                entity = _fetch_entity(client, etype, eid)
                row['name'] = entity.get('name')
                live = entity.get(param)
                same = _same_instant if field == 'end_date' else \
                    (lambda a, b: (None if a is None else str(a)) == (None if b is None else str(b)))

                if same(live, new):
                    row['previous'], row['applied'] = _fmt(old), _fmt(new)
                    row['status'] = 'already_applied'
                elif not same(live, old):
                    row['previous'] = _fmt(None if live is None else str(live))
                    row['status'] = 'stale'
                    row['detail'] = ("live value no longer matches the preview; "
                                     "re-run meta_ads_preview_changes")
                else:
                    client.update_object(eid, {param: new})
                    verify = _fetch_entity(client, etype, eid).get(param)
                    if same(verify, new):
                        row['previous'], row['applied'] = _fmt(old), _fmt(new)
                        row['status'] = 'applied'
                        if field == 'end_date':
                            values = f"{old} -> {new}"
                        else:
                            values = (f"{currency} {_units_str(old, currency)} -> "
                                      f"{_units_str(new, currency)} (raw {old} -> {new})")
                        logger.info('applied %s %s "%s" act=%s %s: %s '
                                    'approved_by=%s batch=%s',
                                    etype, eid, entity.get('name') or '?',
                                    entity.get('account_id') or '?', field, values,
                                    approver or 'password', batch_id)
                    else:
                        row['status'] = 'verify_failed'
                        row['detail'] = f"post-write read returned {verify!r}, expected {new!r}"
                        logger.warning('verify_failed %s %s "%s" act=%s %s: post-write '
                                       'read %r, expected %r batch=%s',
                                       etype, eid, entity.get('name') or '?',
                                       entity.get('account_id') or '?', field,
                                       verify, new, batch_id)
            except FacebookAPIError as e:
                row['status'] = 'error'
                row['detail'] = _error_message(e)
            except Exception as e:  # one bad row must never hide the batch report
                row['status'] = 'error'
                row['detail'] = f"unexpected error: {e}"

            counts[row['status']] = counts.get(row['status'], 0) + 1
            rows.append(row)

        note = ("some rows were not applied; re-run meta_ads_preview_changes for them"
                if any(r['status'] not in ('applied', 'already_applied') for r in rows)
                else None)
        logger.info("batch=%s done %s approved_by=%s", batch_id,
                    ' '.join(f"{k}={v}" for k, v in sorted(counts.items())),
                    approver or 'password')
        header: Dict[str, Any] = {'batch': batch_id, **counts}
        if approver:
            header['approved_by'] = approver
        if format == "json":
            out: Dict[str, Any] = {**header}
            if note:
                out['note'] = note
            out['rows'] = rows
            return _json(out)
        keep = ['entity', 'id', 'name', 'field', 'previous', 'applied', 'status', 'detail']
        return _lines(_kv(header), note, _csv(rows, keep=keep))
    except ToolError:
        raise
    except Exception as e:
        raise ToolError(_error_message(e)) from e


if __name__ == "__main__":
    mcp.run(transport="stdio")

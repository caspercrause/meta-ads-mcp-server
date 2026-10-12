"""
Meta (Facebook) Marketing API client.

Handles authentication, automatic cursor pagination with a page guard,
retry with backoff on transient errors, and typed API errors that carry
Meta's error code so callers can map them to actionable guidance.
"""
from typing import Any, Dict, List, Optional, Sequence, Union
from datetime import datetime, timezone
import urllib.parse
import json
import os
import re
import time

import requests
from dotenv import load_dotenv

load_dotenv()

# Safety guard on cursor pagination: 50 pages x limit 100 = 5000 entities.
MAX_PAGES = 50

# Backoff waits in seconds between retries. Module-level so tests can zero it.
RETRY_WAITS = (3, 8, 15)

# Transient Meta error codes worth retrying: 1/2 unknown or temporary issue,
# 4 application rate limit, 17 user rate limit, 613 custom rate limit,
# 80004 ads insights throttle.
RETRYABLE_CODES = {1, 2, 4, 17, 613, 80004}


class FacebookAPIError(Exception):
    """Meta Graph API error carrying the code and subcode Meta returned."""

    def __init__(
        self,
        message: str,
        code: Optional[int] = None,
        subcode: Optional[int] = None,
        http_status: Optional[int] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.subcode = subcode
        self.http_status = http_status


def _redact(text: str) -> str:
    """Strip access tokens from text destined for error messages or logs."""
    return re.sub(r'access_token=[^&\s]+', 'access_token=REDACTED', text)


def _date_to_unix(date_str: str) -> int:
    """Convert a YYYY-MM-DD string to a Unix epoch (start of day, UTC)."""
    dt = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _build_list_filters(
    name_contains: Optional[str] = None,
    objective: Optional[Union[str, Sequence[str]]] = None,
    created_after: Optional[str] = None,
    created_before: Optional[str] = None,
    updated_after: Optional[str] = None,
    extra_filters: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """
    Compile convenience kwargs into Meta's `filtering` triple array.

    Each filter is `{field, operator, value}` AND'd against the others.
    Date strings (YYYY-MM-DD) are converted to Unix epoch (UTC start of day),
    which is the format Meta's filtering API expects for created_time and
    updated_time fields.

    Operator notes (verified against the Marketing API, not just docs):
    - `name`: only CONTAIN is supported across campaigns / adsets / ads.
        STARTS_WITH returns error 100 on every list endpoint.
    - `objective`: only IN is supported. EQUAL returns error 100.
        A single string is compiled into IN [value]; a list passes through.
    - `created_time` / `updated_time`: GREATER_THAN / LESS_THAN with
        Unix epoch integer values.

    Returns an empty list if no filters were supplied; callers should skip
    setting the `filtering` query param in that case.
    """
    filters: List[Dict[str, Any]] = []
    if name_contains:
        filters.append({'field': 'name', 'operator': 'CONTAIN', 'value': name_contains})
    if objective:
        values = [objective] if isinstance(objective, str) else list(objective)
        filters.append({'field': 'objective', 'operator': 'IN', 'value': values})
    if created_after:
        filters.append({'field': 'created_time', 'operator': 'GREATER_THAN', 'value': _date_to_unix(created_after)})
    if created_before:
        filters.append({'field': 'created_time', 'operator': 'LESS_THAN', 'value': _date_to_unix(created_before)})
    if updated_after:
        filters.append({'field': 'updated_time', 'operator': 'GREATER_THAN', 'value': _date_to_unix(updated_after)})
    if extra_filters:
        filters.extend(extra_filters)
    return filters


class FacebookAdsClient:
    """Client for the Meta Marketing API with pagination and retry built in."""

    def __init__(
        self,
        access_token: Optional[str] = None,
        api_version: Optional[str] = None,
    ) -> None:
        """
        Initialize the client.

        Args:
            access_token: Meta access token (defaults to FACEBOOK_ACCESS_TOKEN env var)
            api_version: Graph API version (defaults to FACEBOOK_API_VERSION env var,
                falling back to v26.0)

        Raises:
            ValueError: If no access token is provided or found in environment
        """
        self.api_version = api_version or os.getenv('FACEBOOK_API_VERSION', 'v26.0')
        self.base_url = f"https://graph.facebook.com/{self.api_version}"

        self._access_token = access_token or os.getenv('FACEBOOK_ACCESS_TOKEN')
        if not self._access_token:
            raise ValueError(
                "Facebook access token must be provided via parameter or "
                "FACEBOOK_ACCESS_TOKEN environment variable"
            )

    def _make_request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Make an authenticated GET request with retry on transient failures.

        Retries on network errors, HTTP 429, HTTP 5xx, and Meta's transient
        error codes (RETRYABLE_CODES), waiting RETRY_WAITS seconds between
        attempts. Non-transient API errors raise immediately.

        Args:
            endpoint: API endpoint path (e.g., "/me/adaccounts")
            params: Query parameters for the request

        Returns:
            JSON response as dictionary

        Raises:
            FacebookAPIError: If the request fails after retries
        """
        url = f"{self.base_url}{endpoint}"
        request_params = dict(params or {})
        request_params['access_token'] = self._access_token

        last_error: Optional[FacebookAPIError] = None
        for attempt in range(len(RETRY_WAITS) + 1):
            if attempt:
                time.sleep(RETRY_WAITS[attempt - 1])
            try:
                response = requests.get(url, params=request_params, timeout=30)
            except requests.exceptions.RequestException as e:
                # requests embeds the full URL (including the access_token
                # query param) in its exception text, so redact before the
                # message can reach tool errors or logs.
                last_error = FacebookAPIError(
                    f"Network error calling Meta API ({type(e).__name__}): {_redact(str(e))}"
                )
                continue

            if response.ok:
                return response.json()

            code: Optional[int] = None
            subcode: Optional[int] = None
            message = response.text[:300]
            try:
                detail = response.json().get('error', {})
                code = detail.get('code')
                subcode = detail.get('error_subcode')
                message = detail.get('message', message)
            except ValueError:
                pass

            last_error = FacebookAPIError(
                message, code=code, subcode=subcode, http_status=response.status_code
            )
            retryable = (
                response.status_code == 429
                or response.status_code >= 500
                or code in RETRYABLE_CODES
            )
            if not retryable:
                raise last_error

        assert last_error is not None
        raise last_error

    def _make_paginated_request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None,
        max_pages: int = MAX_PAGES,
    ) -> Dict[str, Any]:
        """
        Make a paginated GET request, automatically fetching all pages.

        Follows 'paging.next' URLs until exhausted or until max_pages is hit.

        Args:
            endpoint: API endpoint path (e.g., "/me/adaccounts")
            params: Query parameters for the request
            max_pages: Guard against runaway pagination

        Returns:
            Dict with 'data' (combined rows from every fetched page) and
            'pages_truncated' (True when the page guard stopped pagination
            while more pages remained).
        """
        all_data: List[Dict[str, Any]] = []
        response = self._make_request(endpoint, params=params)
        all_data.extend(response.get('data', []))

        pages = 1
        while 'next' in response.get('paging', {}) and pages < max_pages:
            parsed = urllib.parse.urlparse(response['paging']['next'])
            next_params = dict(urllib.parse.parse_qsl(parsed.query))
            response = self._make_request(endpoint, params=next_params)
            all_data.extend(response.get('data', []))
            pages += 1

        truncated = 'next' in response.get('paging', {}) and pages >= max_pages
        return {'data': all_data, 'pages_truncated': truncated}

    # ------------------------------------------------------------------
    # Entity endpoints
    # ------------------------------------------------------------------

    def get_ad_accounts(self) -> Dict[str, Any]:
        """Retrieve all ad accounts accessible by the authenticated user."""
        return self._make_paginated_request("/me/adaccounts", params={
            'fields': 'id,name,currency,timezone_name,account_status,business'
        })

    def get_campaigns(
        self,
        account_id: str,
        effective_status: Optional[List[str]] = None,
        name_contains: Optional[str] = None,
        objective: Optional[Union[str, Sequence[str]]] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        updated_after: Optional[str] = None,
        extra_filters: Optional[List[Dict[str, Any]]] = None,
        limit: int = 100
    ) -> Dict[str, Any]:
        """
        Retrieve all campaigns for the specified ad account.

        Filter kwargs are applied server-side (Meta's `filtering` API) and
        AND'd together. See _build_list_filters for operator quirks.
        """
        account_id = self._normalize_account_id(account_id)
        params: Dict[str, Any] = {
            'fields': 'id,name,effective_status,objective,daily_budget,lifetime_budget,created_time,updated_time',
            'limit': limit
        }
        if effective_status:
            params['effective_status'] = json.dumps(effective_status)

        filters = _build_list_filters(
            name_contains=name_contains,
            objective=objective,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            extra_filters=extra_filters,
        )
        if filters:
            params['filtering'] = json.dumps(filters)

        return self._make_paginated_request(f"/{account_id}/campaigns", params=params)

    DEFAULT_AD_SET_FIELDS = [
        'id',
        'name',
        'campaign_id',
        'effective_status',
        'daily_budget',
        'lifetime_budget',
        'promoted_object',
        'created_time',
        'updated_time',
    ]

    def get_ad_sets(
        self,
        account_id: str,
        campaign_id: Optional[str] = None,
        effective_status: Optional[List[str]] = None,
        extra_fields: Optional[List[str]] = None,
        name_contains: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        updated_after: Optional[str] = None,
        extra_filters: Optional[List[Dict[str, Any]]] = None,
        limit: int = 100
    ) -> Dict[str, Any]:
        """
        Retrieve all ad sets for an ad account or a single campaign.

        When campaign_id is provided the request goes to /{campaign_id}/adsets
        and account_id is ignored. extra_fields are appended to the lean
        default field list (heavy fields like 'targeting' are opt-in).
        """
        field_list = list(self.DEFAULT_AD_SET_FIELDS)
        for f in extra_fields or []:
            if f not in field_list:
                field_list.append(f)
        params: Dict[str, Any] = {
            'fields': ','.join(field_list),
            'limit': limit
        }
        if effective_status:
            params['effective_status'] = json.dumps(effective_status)

        filters = _build_list_filters(
            name_contains=name_contains,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            extra_filters=extra_filters,
        )
        if filters:
            params['filtering'] = json.dumps(filters)

        if campaign_id:
            endpoint = f"/{campaign_id}/adsets"
        else:
            endpoint = f"/{self._normalize_account_id(account_id)}/adsets"

        return self._make_paginated_request(endpoint, params=params)

    def get_ads(
        self,
        account_id: str,
        campaign_id: Optional[str] = None,
        adset_id: Optional[str] = None,
        effective_status: Optional[List[str]] = None,
        include_copy: bool = False,
        name_contains: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        updated_after: Optional[str] = None,
        extra_filters: Optional[List[Dict[str, Any]]] = None,
        limit: int = 100
    ) -> Dict[str, Any]:
        """
        Retrieve all ads for an ad account, campaign, or ad set.

        include_copy adds the creative's title and body to the request;
        by default only the creative id is fetched to keep responses lean.
        """
        creative_sub = 'creative{id,title,body}' if include_copy else 'creative{id}'
        params: Dict[str, Any] = {
            'fields': (
                f'id,name,campaign_id,adset_id,effective_status,{creative_sub},'
                'preview_shareable_link,created_time,updated_time'
            ),
            'limit': limit
        }
        if effective_status:
            params['effective_status'] = json.dumps(effective_status)

        filters = _build_list_filters(
            name_contains=name_contains,
            created_after=created_after,
            created_before=created_before,
            updated_after=updated_after,
            extra_filters=extra_filters,
        )
        if filters:
            params['filtering'] = json.dumps(filters)

        if adset_id:
            endpoint = f"/{adset_id}/ads"
        elif campaign_id:
            endpoint = f"/{campaign_id}/ads"
        else:
            endpoint = f"/{self._normalize_account_id(account_id)}/ads"

        return self._make_paginated_request(endpoint, params=params)

    def get_ad_creatives(self, creative_ids: Sequence[str]) -> Dict[str, Any]:
        """
        Retrieve multiple AdCreative objects in one batched request.

        Uses the Graph API's `?ids=a,b,c` form, which returns a dict keyed
        by creative id. Meta rejects the whole request if any id is invalid.
        """
        params = {
            'ids': ','.join(creative_ids),
            'fields': 'body,title,object_story_spec,asset_feed_spec,image_url'
        }
        return self._make_request("/", params=params)

    # ------------------------------------------------------------------
    # Insights
    # ------------------------------------------------------------------

    def get_insights(
        self,
        account_id: str,
        start_date: str,
        end_date: str,
        fields: List[str],
        level: str = "account",
        breakdowns: Optional[List[str]] = None,
        time_increment: Optional[str] = None,
        campaign_ids: Optional[List[str]] = None,
        adset_ids: Optional[List[str]] = None,
        ad_ids: Optional[List[str]] = None,
        attribution_windows: Optional[List[str]] = None,
        limit: int = 100
    ) -> Dict[str, Any]:
        """
        Retrieve performance insights at any level with automatic pagination.

        The date range is sent as a time_range JSON object (since/until,
        both inclusive), which is the form Meta respects reliably.
        """
        account_id = self._normalize_account_id(account_id)
        params: Dict[str, Any] = {
            'fields': ','.join(fields),
            'level': level,
            'time_range': json.dumps({'since': start_date, 'until': end_date}),
            'limit': limit
        }
        if breakdowns:
            params['breakdowns'] = ','.join(breakdowns)
        if time_increment:
            params['time_increment'] = time_increment
        if attribution_windows:
            params['action_attribution_windows'] = json.dumps(list(attribution_windows))

        filtering = []
        if campaign_ids:
            filtering.append({'field': 'campaign.id', 'operator': 'IN', 'value': campaign_ids})
        if adset_ids:
            filtering.append({'field': 'adset.id', 'operator': 'IN', 'value': adset_ids})
        if ad_ids:
            filtering.append({'field': 'ad.id', 'operator': 'IN', 'value': ad_ids})
        if filtering:
            params['filtering'] = json.dumps(filtering)

        return self._make_paginated_request(f"/{account_id}/insights", params=params)

    # ------------------------------------------------------------------
    # Pixel and custom conversion endpoints
    # ------------------------------------------------------------------

    def get_pixels(self, account_id: str, limit: int = 100) -> Dict[str, Any]:
        """
        Retrieve all Meta Pixels (datasets) on an ad account.

        owner_ad_account{id,name} is intentionally excluded: that sub-query
        reads the linked ad account's metadata, and Meta rejects the entire
        request with error 200 if the token lacks read access on the linked
        account, even when the caller has full access to the account being
        queried. The owning account is usually the same as account_id.
        """
        account_id = self._normalize_account_id(account_id)
        params = {
            'fields': (
                'id,name,last_fired_time,creation_time,is_unavailable,'
                'is_created_by_business,data_use_setting,'
                'enable_automatic_matching,first_party_cookie_status,'
                'can_proxy'
            ),
            'limit': limit
        }
        return self._make_paginated_request(f"/{account_id}/adspixels", params=params)

    def get_pixel_stats(
        self,
        pixel_id: str,
        start_time: str,
        end_time: str,
        aggregation: str = 'event',
        event: Optional[str] = None,
        limit: int = 100
    ) -> Dict[str, Any]:
        """
        Retrieve event stats for a pixel, bucketed by an aggregation dimension.

        Aggregation values are deliberately not allowlisted client-side: Meta
        keeps adding new ones, and an invalid value returns an error listing
        the currently valid options.
        """
        params: Dict[str, Any] = {
            'aggregation': aggregation,
            'start_time': start_time,
            'end_time': end_time,
            'limit': limit
        }
        if event:
            params['event'] = event
        return self._make_paginated_request(f"/{pixel_id}/stats", params=params)

    def get_pixel_da_checks(self, pixel_id: str) -> Dict[str, Any]:
        """Retrieve diagnostic checks (DACheck) for a pixel."""
        return self._make_request(f"/{pixel_id}/da_checks")

    def get_pixel_dataset_quality(self, pixel_id: str) -> Dict[str, Any]:
        """
        Retrieve dataset quality fields for a pixel.

        May require business_management permission or a System User token;
        callers should degrade gracefully when this raises.
        """
        params = {
            'fields': (
                'id,name,last_fired_time,automatic_matching_fields,'
                'enable_automatic_matching,first_party_cookie_status,'
                'data_use_setting'
            )
        }
        return self._make_request(f"/{pixel_id}", params=params)

    def get_custom_conversions(self, account_id: str, limit: int = 100) -> Dict[str, Any]:
        """Retrieve all custom conversions for an ad account (Meta caps these at 100)."""
        account_id = self._normalize_account_id(account_id)
        params = {
            'fields': (
                'id,name,description,custom_event_type,rule,event_source_id,'
                'first_fired_time,last_fired_time,is_archived,is_unavailable,'
                'retention_days,default_conversion_value,creation_time'
            ),
            'limit': limit
        }
        return self._make_paginated_request(f"/{account_id}/customconversions", params=params)

    def get_custom_conversion_stats(
        self,
        custom_conversion_id: str,
        start_time: str,
        end_time: str,
        aggregation: str = 'count',
        limit: int = 100
    ) -> Dict[str, Any]:
        """Retrieve stats for a single custom conversion."""
        params: Dict[str, Any] = {
            'aggregation': aggregation,
            'start_time': start_time,
            'end_time': end_time,
            'limit': limit
        }
        return self._make_paginated_request(f"/{custom_conversion_id}/stats", params=params)

    @staticmethod
    def _normalize_account_id(account_id: str) -> str:
        return account_id if account_id.startswith('act_') else f'act_{account_id}'

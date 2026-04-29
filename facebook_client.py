"""
Facebook Ads API Client with automatic pagination support.

This module provides a client for interacting with Facebook Marketing API,
handling authentication, pagination, and error management.
"""
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
import urllib.parse
import requests
import json
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


def _date_to_unix(date_str: str) -> int:
    """Convert a YYYY-MM-DD string to a Unix epoch (start of day, UTC)."""
    dt = datetime.strptime(date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _build_list_filters(
    name_contains: Optional[str] = None,
    objective: Optional[str] = None,
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
        We compile a single `objective` value into IN [value] for ergonomics.
    - `created_time` / `updated_time`: GREATER_THAN / LESS_THAN with
        Unix epoch integer values.

    Returns an empty list if no filters were supplied; callers should skip
    setting the `filtering` query param in that case.
    """
    filters: List[Dict[str, Any]] = []
    if name_contains:
        filters.append({'field': 'name', 'operator': 'CONTAIN', 'value': name_contains})
    if objective:
        filters.append({'field': 'objective', 'operator': 'IN', 'value': [objective]})
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
    """
    Client for Facebook Marketing API with automatic pagination support.

    This client handles all API interactions with Facebook's Graph API,
    including automatic pagination, error handling, and token management.
    """

    def __init__(
        self,
        access_token: Optional[str] = None,
        api_version: str = "v24.0"
    ) -> None:
        """
        Initialize Facebook Ads API client.

        Args:
            access_token: Facebook access token (defaults to FACEBOOK_ACCESS_TOKEN env var)
            api_version: Facebook API version (default: "v24.0")

        Raises:
            ValueError: If no access token is provided or found in environment
        """
        self.api_version = api_version
        self.base_url = f"https://graph.facebook.com/{api_version}"

        # Get access token from parameter or environment
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
        Make authenticated API request with error handling.

        Args:
            endpoint: API endpoint path (e.g., "/me/adaccounts")
            params: Query parameters for the request

        Returns:
            JSON response as dictionary

        Raises:
            requests.exceptions.RequestException: If API request fails
        """
        url = f"{self.base_url}{endpoint}"
        request_params = params or {}
        request_params['access_token'] = self._access_token

        try:
            response = requests.get(url, params=request_params, timeout=30)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            error_msg = f"Facebook API request failed: {e}"
            try:
                error_json = response.json()
                if 'error' in error_json:
                    error_detail = error_json['error']
                    error_msg = (
                        f"Facebook API Error {error_detail.get('code', 'Unknown')}: "
                        f"{error_detail.get('message', str(e))}"
                    )
                    if 'error_subcode' in error_detail:
                        error_msg += f" (Subcode: {error_detail['error_subcode']})"
            except:
                pass
            raise requests.exceptions.RequestException(error_msg)

    def _make_paginated_request(
        self,
        endpoint: str,
        params: Optional[Dict[str, Any]] = None
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Make paginated GET request, automatically fetching all pages.

        This method handles Facebook's cursor-based pagination by following
        'paging.next' URLs until all pages are retrieved. This is the critical
        feature that distinguishes this implementation from naive approaches.

        Args:
            endpoint: API endpoint path (e.g., "/me/adaccounts")
            params: Query parameters for the request

        Returns:
            Dictionary with 'data' key containing combined results from all pages

        Raises:
            requests.exceptions.RequestException: If API request fails
        """
        all_data: List[Dict[str, Any]] = []
        response = self._make_request(endpoint, params=params)
        all_data.extend(response.get('data', []))

        # Fetch all pages automatically
        while 'paging' in response and 'next' in response['paging']:
            next_url = response['paging']['next']
            parsed = urllib.parse.urlparse(next_url)
            next_params = dict(urllib.parse.parse_qsl(parsed.query))
            response = self._make_request(endpoint, params=next_params)
            all_data.extend(response.get('data', []))

        return {'data': all_data}

    def get_ad_accounts(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Retrieve all ad accounts accessible by the authenticated user.

        Returns all accounts automatically with pagination handled internally.

        Returns:
            Dictionary containing list of all ad accounts with keys:
            - id: Account ID with 'act_' prefix
            - account_id: Numeric account ID
            - name: Account name
            - currency: Account currency code
            - timezone_name: Account timezone
            - account_status: 1=Active, 101=Disabled

        Example:
            >>> client = FacebookAdsClient()
            >>> accounts = client.get_ad_accounts()
            >>> for account in accounts['data']:
            ...     print(f"{account['name']}: {account['id']}")
        """
        return self._make_paginated_request("/me/adaccounts", params={
            'fields': 'id,name,account_id,currency,timezone_name,account_status,business'
        })

    def get_campaigns(
        self,
        account_id: str,
        effective_status: Optional[List[str]] = None,
        name_contains: Optional[str] = None,
        objective: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        updated_after: Optional[str] = None,
        extra_filters: Optional[List[Dict[str, Any]]] = None,
        limit: int = 100
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Retrieve all campaigns for specified ad account.

        Returns all campaigns automatically with pagination handled internally.
        All filter kwargs are applied server-side (Meta's `filtering` API)
        and AND'd together.

        Args:
            account_id: Facebook ad account ID (with or without 'act_' prefix)
            effective_status: Filter by campaign status (e.g., ['ACTIVE', 'PAUSED'])
            name_contains: Server-side substring match on campaign name
                (CASE-SENSITIVE). Only operator supported on Meta's side
                for the name field; for prefix matching, use a leading
                anchor in the substring.
            objective: Filter to a single objective (e.g. 'OUTCOME_LEADS').
                Compiled to Meta's IN operator with a one-element list
                (Meta does not accept EQUAL on objective).
            created_after: YYYY-MM-DD - only campaigns created strictly after
                this date (UTC start of day).
            created_before: YYYY-MM-DD - only campaigns created strictly before
                this date.
            updated_after: YYYY-MM-DD - only campaigns updated strictly after.
            extra_filters: Escape hatch - raw Meta filter triples
                ({field, operator, value}) AND'd with the convenience filters.
            limit: Results per page for internal batching (default: 100)

        Returns:
            Dictionary containing list of all campaigns matching the filters.
        """
        if not account_id.startswith('act_'):
            account_id = f'act_{account_id}'

        params = {
            'fields': 'id,name,status,effective_status,objective,daily_budget,lifetime_budget,created_time,updated_time',
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

        return self._make_paginated_request(
            f"/{account_id}/campaigns",
            params=params
        )

    DEFAULT_AD_SET_FIELDS = [
        'id',
        'name',
        'campaign_id',
        'status',
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
        fields: Optional[List[str]] = None,
        name_contains: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        updated_after: Optional[str] = None,
        extra_filters: Optional[List[Dict[str, Any]]] = None,
        limit: int = 100
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Retrieve all ad sets for specified ad account or campaign.

        Returns all ad sets automatically with pagination handled internally.
        Filter kwargs are applied server-side (Meta's `filtering` API) and
        AND'd together.

        Args:
            account_id: Facebook ad account ID (with or without 'act_' prefix).
                Used when campaign_id is not provided.
            campaign_id: Optional campaign ID to scope ad sets to a single
                campaign. When provided, hits /{campaign_id}/adsets and
                account_id is ignored.
            effective_status: Filter by ad set status
            fields: Override the default field list. Pass None to use
                DEFAULT_AD_SET_FIELDS (lean, no targeting). Heavy fields like
                'targeting' can multiply response size by 10x or more.
            name_contains: Server-side substring match (CASE-SENSITIVE).
                Only operator supported on Meta's side for `name` -
                STARTS_WITH is rejected with error 100.
            created_after: YYYY-MM-DD floor on created_time.
            created_before: YYYY-MM-DD ceiling on created_time.
            updated_after: YYYY-MM-DD floor on updated_time.
            extra_filters: Escape hatch - raw Meta filter triples AND'd in.
            limit: Results per page for internal batching (default: 100)

        Returns:
            Dictionary containing list of all ad sets matching the filters.
        """
        field_list = fields if fields is not None else self.DEFAULT_AD_SET_FIELDS
        params = {
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
            if not account_id.startswith('act_'):
                account_id = f'act_{account_id}'
            endpoint = f"/{account_id}/adsets"

        return self._make_paginated_request(endpoint, params=params)

    def get_ads(
        self,
        account_id: str,
        campaign_id: Optional[str] = None,
        adset_id: Optional[str] = None,
        effective_status: Optional[List[str]] = None,
        name_contains: Optional[str] = None,
        created_after: Optional[str] = None,
        created_before: Optional[str] = None,
        updated_after: Optional[str] = None,
        extra_filters: Optional[List[Dict[str, Any]]] = None,
        limit: int = 100
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Retrieve all ads for specified ad account, campaign, or ad set.

        Returns all ads automatically with pagination handled internally.
        Filter kwargs are applied server-side (Meta's `filtering` API) and
        AND'd together.

        Args:
            account_id: Facebook ad account ID (with or without 'act_' prefix)
            campaign_id: Optional campaign ID scope.
            adset_id: Optional ad set ID scope.
            effective_status: Filter by ad status.
            name_contains: Server-side substring match (CASE-SENSITIVE).
                Only operator supported on Meta's side for `name` -
                STARTS_WITH is rejected with error 100.
            created_after: YYYY-MM-DD floor on created_time.
            created_before: YYYY-MM-DD ceiling on created_time.
            updated_after: YYYY-MM-DD floor on updated_time.
            extra_filters: Escape hatch - raw Meta filter triples AND'd in.
            limit: Results per page for internal batching (default: 100)

        Returns:
            Dictionary containing list of all ads matching the filters.
        """
        params = {
            'fields': 'id,name,campaign_id,adset_id,status,effective_status,creative{id,title,body,image_url},preview_shareable_link,created_time,updated_time',
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
            if not account_id.startswith('act_'):
                account_id = f'act_{account_id}'
            endpoint = f"/{account_id}/ads"

        return self._make_paginated_request(endpoint, params=params)

    def get_ad_creative(self, creative_id: str) -> Dict[str, Any]:
        """
        Retrieve the full AdCreative object for a given creative ID.

        Single-resource fetch (not paginated). Use list_ads to discover
        the creative_id from each ad's nested `creative.id` field.

        Args:
            creative_id: AdCreative ID (e.g. from list_ads -> creative.id)

        Returns:
            Raw API response containing the requested fields:
            - body: Primary ad text (top level)
            - title: Headline (top level)
            - object_story_spec: Full story spec. For carousels,
                link_data.child_attachments holds per-slide text.
            - asset_feed_spec: Dynamic / flexible creative asset
                structure (titles, bodies, images, videos as parallel
                arrays).
            - image_url: Thumbnail URL. Often null for story-spec or
                asset-feed creatives since the real image is nested
                inside the spec.
        """
        params = {
            'fields': 'body,title,object_story_spec,asset_feed_spec,image_url'
        }
        return self._make_request(f"/{creative_id}", params=params)

    def get_account_insights(
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
        limit: int = 100
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Retrieve performance insights for specified ad account.

        Returns all insights automatically with pagination handled internally.

        Args:
            account_id: Facebook ad account ID (with or without 'act_' prefix)
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            fields: List of metrics to retrieve (e.g., ['spend', 'impressions', 'clicks'])
            level: Aggregation level ('account', 'campaign', 'adset', or 'ad')
            breakdowns: Optional breakdowns (e.g., ['age', 'gender'])
            time_increment: Time granularity ('1' for daily, 'all_days' for total)
            campaign_ids: Optional list of campaign IDs to filter by
            adset_ids: Optional list of ad set IDs to filter by
            ad_ids: Optional list of ad IDs to filter by
            limit: Results per page for internal batching (default: 100)

        Returns:
            Dictionary containing list of all insights rows

        Example:
            >>> client = FacebookAdsClient()
            >>> insights = client.get_account_insights(
            ...     account_id='123456',
            ...     start_date='2025-01-01',
            ...     end_date='2025-01-31',
            ...     fields=['campaign_name', 'spend', 'impressions', 'clicks'],
            ...     level='campaign',
            ...     campaign_ids=['123', '456']  # Filter by specific campaigns
            ... )
            >>> print(f"Retrieved {len(insights['data'])} insights rows")
        """
        if not account_id.startswith('act_'):
            account_id = f'act_{account_id}'

        params = {
            'fields': ','.join(fields),
            'level': level,
            'time_range': json.dumps({'since': start_date, 'until': end_date}),
            'limit': limit
        }

        if breakdowns:
            params['breakdowns'] = ','.join(breakdowns)

        if time_increment:
            params['time_increment'] = time_increment

        # Build filtering parameter for campaign/adset/ad IDs
        filtering = []
        if campaign_ids:
            filtering.append({
                'field': 'campaign.id',
                'operator': 'IN',
                'value': campaign_ids
            })
        if adset_ids:
            filtering.append({
                'field': 'adset.id',
                'operator': 'IN',
                'value': adset_ids
            })
        if ad_ids:
            filtering.append({
                'field': 'ad.id',
                'operator': 'IN',
                'value': ad_ids
            })
        
        if filtering:
            params['filtering'] = json.dumps(filtering)

        return self._make_paginated_request(
            f"/{account_id}/insights",
            params=params
        )

    # ------------------------------------------------------------------
    # Pixel and Custom Conversion endpoints
    # ------------------------------------------------------------------

    def get_pixels(
        self,
        account_id: str,
        limit: int = 100
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Retrieve all Meta Pixels (datasets) on an ad account.

        Args:
            account_id: Facebook ad account ID (with or without 'act_' prefix)
            limit: Results per page for internal batching (default: 100)

        Returns:
            Dictionary containing list of all pixels with keys:
            - id: Pixel/dataset ID (use this for pixel-stats and health calls)
            - name: Pixel name
            - last_fired_time: ISO timestamp of the most recent event received
            - creation_time: ISO timestamp when the pixel was created
            - is_unavailable: True if the pixel is no longer accessible
            - is_created_by_business: True if owned by a Business Manager
            - data_use_setting: ADVERTISING_AND_ANALYTICS or ANALYTICS_ONLY
            - enable_automatic_matching: Whether Advanced Matching is on
            - first_party_cookie_status: FIRST_PARTY_COOKIE_ENABLED / DISABLED
        """
        if not account_id.startswith('act_'):
            account_id = f'act_{account_id}'

        # owner_ad_account{id,name} is intentionally excluded: that sub-query
        # reads the linked ad account's metadata, and Meta rejects the entire
        # request with error 200 if the token lacks read access on the linked
        # account, even when the caller has full access to the account being
        # queried. The owning account is usually the same as account_id.
        params = {
            'fields': (
                'id,name,last_fired_time,creation_time,is_unavailable,'
                'is_created_by_business,data_use_setting,'
                'enable_automatic_matching,first_party_cookie_status,'
                'can_proxy'
            ),
            'limit': limit
        }

        return self._make_paginated_request(
            f"/{account_id}/adspixels",
            params=params
        )

    def get_pixel_stats(
        self,
        pixel_id: str,
        start_time: str,
        end_time: str,
        aggregation: str = 'event',
        event: Optional[str] = None,
        limit: int = 100
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Retrieve event stats for a Meta Pixel, bucketed by an aggregation dimension.

        Args:
            pixel_id: Meta Pixel/dataset ID
            start_time: ISO 8601 string or Unix epoch (e.g. "2025-01-01")
            end_time: ISO 8601 string or Unix epoch
            aggregation: One of 'event', 'browser_type', 'device_os',
                'device_type', 'host', 'pixel_fire', 'url',
                'custom_data_field', 'placement'
            event: Optional event name to filter to (e.g. 'Purchase', 'Lead')
            limit: Results per page for internal batching

        Returns:
            Dictionary with 'data' key containing stat rows. Each row has:
            - start_time: Bucket start time
            - value: Numeric count for the bucket
            - data: Bucketed dimension breakdown (depends on aggregation)
        """
        params: Dict[str, Any] = {
            'aggregation': aggregation,
            'start_time': start_time,
            'end_time': end_time,
            'limit': limit
        }
        if event:
            params['event'] = event

        return self._make_paginated_request(
            f"/{pixel_id}/stats",
            params=params
        )

    def get_pixel_da_checks(self, pixel_id: str) -> Dict[str, Any]:
        """
        Retrieve diagnostic checks (DACheck) for a Meta Pixel.

        Each check reports a key, description, result (PASS/WARN/FAIL),
        and a user-facing message. Useful as a quick health snapshot.

        Args:
            pixel_id: Meta Pixel/dataset ID

        Returns:
            Raw API response with 'data' list of DACheck objects
        """
        return self._make_request(f"/{pixel_id}/da_checks")

    def get_pixel_dataset_quality(self, pixel_id: str) -> Dict[str, Any]:
        """
        Retrieve dataset quality fields for a Meta Pixel.

        Reads the pixel object with the extended diagnostic fields that are
        publicly exposed on the Graph API. Note that the full Event Match
        Quality (EMQ) summary is not available via this endpoint - see the
        Dataset Quality dashboard in Events Manager for that.

        Args:
            pixel_id: Meta Pixel/dataset ID

        Returns:
            Raw API response with available diagnostic fields
        """
        params = {
            'fields': (
                'id,name,last_fired_time,automatic_matching_fields,'
                'enable_automatic_matching,first_party_cookie_status,'
                'data_use_setting'
            )
        }
        return self._make_request(f"/{pixel_id}", params=params)

    def get_custom_conversions(
        self,
        account_id: str,
        limit: int = 100
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Retrieve all custom conversions for an ad account.

        Args:
            account_id: Facebook ad account ID (with or without 'act_' prefix)
            limit: Results per page for internal batching

        Returns:
            Dictionary containing list of custom conversions with keys:
            - id: Custom conversion ID
            - name: Display name
            - description: Optional description
            - custom_event_type: PURCHASE, LEAD, ADD_TO_CART, OTHER, ...
            - rule: JSON rule string used to match events
            - event_source_id: Linked pixel ID
            - first_fired_time: First time this conversion fired
            - last_fired_time: Most recent fire (cheapest "is it broken?" signal)
            - is_archived: True if archived
            - is_unavailable: True if no longer accessible
            - retention_days: Days events are retained
            - default_conversion_value: Default monetary value
        """
        if not account_id.startswith('act_'):
            account_id = f'act_{account_id}'

        params = {
            'fields': (
                'id,name,description,custom_event_type,rule,event_source_id,'
                'first_fired_time,last_fired_time,is_archived,is_unavailable,'
                'retention_days,default_conversion_value,creation_time'
            ),
            'limit': limit
        }

        return self._make_paginated_request(
            f"/{account_id}/customconversions",
            params=params
        )

    def get_custom_conversion_stats(
        self,
        custom_conversion_id: str,
        start_time: str,
        end_time: str,
        aggregation: str = 'count',
        limit: int = 100
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Retrieve stats for a single custom conversion.

        Args:
            custom_conversion_id: Custom conversion ID
            start_time: ISO 8601 string or Unix epoch
            end_time: ISO 8601 string or Unix epoch
            aggregation: Aggregation mode (e.g. 'count', 'value', 'count_unique_users')
            limit: Results per page for internal batching

        Returns:
            Dictionary with 'data' key containing stat rows
        """
        params: Dict[str, Any] = {
            'aggregation': aggregation,
            'start_time': start_time,
            'end_time': end_time,
            'limit': limit
        }

        return self._make_paginated_request(
            f"/{custom_conversion_id}/stats",
            params=params
        )

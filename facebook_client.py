"""
Facebook Ads API Client with automatic pagination support.

This module provides a client for interacting with Facebook Marketing API,
handling authentication, pagination, and error management.
"""
from typing import Dict, List, Any, Optional
import urllib.parse
import requests
import json
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class FacebookAdsClient:
    """
    Client for Facebook Marketing API with automatic pagination support.

    This client handles all API interactions with Facebook's Graph API,
    including automatic pagination, error handling, and token management.
    """

    def __init__(
        self,
        access_token: Optional[str] = None,
        api_version: str = "v22.0"
    ) -> None:
        """
        Initialize Facebook Ads API client.

        Args:
            access_token: Facebook access token (defaults to FACEBOOK_ACCESS_TOKEN env var)
            api_version: Facebook API version (default: "v22.0")

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
        limit: int = 100
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Retrieve all campaigns for specified ad account.

        Returns all campaigns automatically with pagination handled internally.

        Args:
            account_id: Facebook ad account ID (with or without 'act_' prefix)
            effective_status: Filter by campaign status (e.g., ['ACTIVE', 'PAUSED'])
            limit: Results per page for internal batching (default: 100)

        Returns:
            Dictionary containing list of all campaigns

        Example:
            >>> client = FacebookAdsClient()
            >>> campaigns = client.get_campaigns(
            ...     account_id='123456',
            ...     effective_status=['ACTIVE']
            ... )
            >>> print(f"Found {len(campaigns['data'])} campaigns")
        """
        if not account_id.startswith('act_'):
            account_id = f'act_{account_id}'

        params = {
            'fields': 'id,name,status,effective_status,objective,daily_budget,lifetime_budget,created_time,updated_time',
            'limit': limit
        }

        if effective_status:
            params['effective_status'] = json.dumps(effective_status)

        return self._make_paginated_request(
            f"/{account_id}/campaigns",
            params=params
        )

    def get_ad_sets(
        self,
        account_id: str,
        effective_status: Optional[List[str]] = None,
        limit: int = 100
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Retrieve all ad sets for specified ad account.

        Returns all ad sets automatically with pagination handled internally.

        Args:
            account_id: Facebook ad account ID (with or without 'act_' prefix)
            effective_status: Filter by ad set status
            limit: Results per page for internal batching (default: 100)

        Returns:
            Dictionary containing list of all ad sets
        """
        if not account_id.startswith('act_'):
            account_id = f'act_{account_id}'

        params = {
            'fields': 'id,name,status,effective_status,daily_budget,lifetime_budget,targeting,created_time,updated_time',
            'limit': limit
        }

        if effective_status:
            params['effective_status'] = json.dumps(effective_status)

        return self._make_paginated_request(
            f"/{account_id}/adsets",
            params=params
        )

    def get_ads(
        self,
        account_id: str,
        effective_status: Optional[List[str]] = None,
        limit: int = 100
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Retrieve all ads for specified ad account.

        Returns all ads automatically with pagination handled internally.

        Args:
            account_id: Facebook ad account ID (with or without 'act_' prefix)
            effective_status: Filter by ad status
            limit: Results per page for internal batching (default: 100)

        Returns:
            Dictionary containing list of all ads
        """
        if not account_id.startswith('act_'):
            account_id = f'act_{account_id}'

        params = {
            'fields': 'id,name,status,effective_status,creative{id,title,body,image_url},created_time,updated_time',
            'limit': limit
        }

        if effective_status:
            params['effective_status'] = json.dumps(effective_status)

        return self._make_paginated_request(
            f"/{account_id}/ads",
            params=params
        )

    def get_account_insights(
        self,
        account_id: str,
        start_date: str,
        end_date: str,
        fields: List[str],
        level: str = "account",
        breakdowns: Optional[List[str]] = None,
        time_increment: Optional[str] = None,
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
            ...     level='campaign'
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

        return self._make_paginated_request(
            f"/{account_id}/insights",
            params=params
        )

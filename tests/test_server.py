"""Offline tool tests: a fake Graph API stands in for Meta."""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import facebook_client  # noqa: E402
import server  # noqa: E402
from facebook_client import FacebookAdsClient, FacebookAPIError  # noqa: E402
from fastmcp import Client  # noqa: E402

ACCOUNTS_PAGE_1 = {
    'data': [
        {'id': 'act_1', 'name': 'Alpha Account', 'currency': 'CHF',
         'timezone_name': 'Europe/Zurich', 'account_status': 1,
         'business': {'id': '9', 'name': 'Alpha Business'}},
        {'id': 'act_2', 'name': 'Beta Account', 'currency': 'EUR',
         'timezone_name': 'Europe/Paris', 'account_status': 101},
    ],
    'paging': {'next': 'https://graph.facebook.com/v26.0/me/adaccounts?after=cursor2'},
}
ACCOUNTS_PAGE_2 = {
    'data': [
        {'id': 'act_3', 'name': 'Gamma Account', 'currency': 'USD',
         'timezone_name': 'America/New_York', 'account_status': 1},
    ],
}

CAMPAIGNS = {
    'data': [
        {'id': '101', 'name': 'Leads CH', 'effective_status': 'ACTIVE',
         'objective': 'OUTCOME_LEADS', 'daily_budget': '50000',
         'created_time': '2026-01-05T10:00:00+0100',
         'updated_time': '2026-02-01T09:30:00+0100'},
        {'id': '102', 'name': 'Sales FR', 'effective_status': 'ACTIVE',
         'objective': 'OUTCOME_SALES', 'lifetime_budget': '123456',
         'created_time': '2026-01-06T10:00:00+0100',
         'updated_time': '2026-02-02T09:30:00+0100'},
    ],
}

ADSETS = {
    'data': [
        {'id': '201', 'name': 'AS One', 'campaign_id': '101',
         'effective_status': 'ACTIVE', 'daily_budget': '2500',
         'promoted_object': {'pixel_id': '777', 'custom_event_type': 'LEAD'},
         'created_time': '2026-01-05T10:00:00+0100',
         'updated_time': '2026-01-05T10:00:00+0100'},
    ],
}

ADS = {
    'data': [
        {'id': '301', 'name': 'Ad One', 'campaign_id': '101', 'adset_id': '201',
         'effective_status': 'ACTIVE', 'creative': {'id': '401'},
         'preview_shareable_link': 'https://fb.me/abc',
         'created_time': '2026-01-05T10:00:00+0100',
         'updated_time': '2026-01-05T10:00:00+0100'},
    ],
}

CREATIVES = {
    '401': {
        'id': '401',
        'body': 'Primary text here',
        'object_story_spec': {
            'link_data': {
                'message': 'Primary text here',
                'name': 'Headline here',
                'link': 'https://example.com',
                'call_to_action': {'type': 'LEARN_MORE'},
                'child_attachments': [
                    {'name': 'Slide one headline', 'description': 'Slide one desc'},
                    {'name': 'Slide two headline'},
                ],
            },
        },
    },
    '402': {
        'id': '402',
        'asset_feed_spec': {
            'bodies': [{'text': 'AFS body A'}, {'text': 'AFS body B'}],
            'titles': [{'text': 'AFS title A'}],
            'call_to_action_types': ['SIGN_UP'],
        },
    },
}

INSIGHTS = {
    'data': [
        {'campaign_id': '101', 'campaign_name': 'Leads CH', 'spend': '100.50',
         'impressions': '20000', 'reach': '15000', 'inline_link_clicks': '400',
         'actions': [{'action_type': 'lead', 'value': '12'},
                     {'action_type': 'link_click', 'value': '400'}],
         'date_start': '2026-01-01', 'date_stop': '2026-01-31'},
        {'campaign_id': '102', 'campaign_name': 'Sales FR', 'spend': '50.25',
         'impressions': '10000', 'reach': '8000', 'inline_link_clicks': '100',
         'conversions': {'fb_pixel_custom.all_leads': '7'},
         'date_start': '2026-01-01', 'date_stop': '2026-01-31'},
    ],
}

# Live /stats shape: `value` is the dimension label (the event name here)
# and `count` is the number. Treating `value` as the number is the bug that
# zeroed every row in production.
PIXEL_STATS = {
    'data': [
        {'start_time': '2026-04-14T03:00:00+0000',
         'data': [{'value': 'Lead', 'count': 5}, {'value': 'PageView', 'count': 90}]},
        {'start_time': '2026-04-14T04:00:00+0000',
         'data': [{'value': 'Lead', 'count': 3}]},
        {'start_time': '2026-04-15T03:00:00+0000',
         'data': [{'value': 'Lead', 'count': 2}]},
    ],
}

CC_STATS = {
    'data': [
        {'timestamp': '2026-04-14T03:00:00+0000', 'data': [{'count': 4}]},
        {'timestamp': '2026-04-14T09:00:00+0000', 'data': [{'count': 6}]},
    ],
}

DA_CHECKS = {
    'data': [
        {'key': 'pixel_last_fired', 'result': 'PASS', 'description': 'Pixel fired recently'},
        {'key': 'event_coverage', 'result': 'FAIL', 'user_message': 'Missing Purchase event'},
    ],
}

DATASET_QUALITY = {
    'id': '777', 'name': 'Main Pixel', 'last_fired_time': '2026-04-15T10:00:00+0000',
    'enable_automatic_matching': True, 'first_party_cookie_status': 'FIRST_PARTY_COOKIE_ENABLED',
    'data_use_setting': 'ADVERTISING_AND_ANALYTICS',
}

CUSTOM_CONVERSIONS = {
    'data': [
        {'id': '501', 'name': 'All Leads', 'custom_event_type': 'LEAD',
         'rule': '{"url":{"i_contains":"thanks"}}', 'event_source_id': '777',
         'first_fired_time': '2025-05-01T00:00:00+0000',
         'last_fired_time': '2026-04-15T09:00:00+0000',
         'is_archived': False, 'retention_days': 90},
        {'id': '502', 'name': 'Old Conversion', 'custom_event_type': 'OTHER',
         'rule': '{}', 'event_source_id': '777', 'is_archived': True},
    ],
}


class FakeAPI:
    """Stands in for FacebookAdsClient._make_request."""

    def __init__(self):
        self.calls = []
        self.fail = None
        self.overrides = {}  # endpoint suffix -> payload or callable(params)

    def __call__(self, endpoint, params=None):
        params = dict(params or {})
        self.calls.append((endpoint, params))
        if self.fail:
            err, self.fail = self.fail, None
            raise err
        for suffix, payload in self.overrides.items():
            if endpoint.endswith(suffix):
                return payload(params) if callable(payload) else payload
        if endpoint == '/me/adaccounts':
            return ACCOUNTS_PAGE_2 if params.get('after') else ACCOUNTS_PAGE_1
        if endpoint.endswith('/campaigns'):
            return CAMPAIGNS
        if endpoint.endswith('/adsets'):
            return ADSETS
        if endpoint.endswith('/ads'):
            return ADS
        if endpoint == '/':
            return {cid: CREATIVES[cid] for cid in params['ids'].split(',')}
        if endpoint.endswith('/insights'):
            return INSIGHTS
        if endpoint.endswith('/adspixels'):
            return {'data': [{'id': '777', 'name': 'Main Pixel',
                              'last_fired_time': '2026-04-15T10:00:00+0000',
                              'is_unavailable': False,
                              'data_use_setting': 'ADVERTISING_AND_ANALYTICS',
                              'enable_automatic_matching': True,
                              'first_party_cookie_status': 'FIRST_PARTY_COOKIE_ENABLED'}]}
        if endpoint == '/777/stats':
            return PIXEL_STATS
        if endpoint == '/501/stats':
            return CC_STATS
        if endpoint == '/777/da_checks':
            return DA_CHECKS
        if endpoint == '/777':
            return DATASET_QUALITY
        if endpoint.endswith('/customconversions'):
            return CUSTOM_CONVERSIONS
        raise AssertionError(f'unexpected endpoint {endpoint}')

    def last_params(self, endpoint_suffix):
        for ep, params in reversed(self.calls):
            if ep.endswith(endpoint_suffix):
                return params
        raise AssertionError(f'no call to {endpoint_suffix}')


@pytest.fixture
def fake(monkeypatch):
    f = FakeAPI()
    c = FacebookAdsClient(access_token='test-token')
    c._make_request = f
    monkeypatch.setattr(server, '_client', c)
    return f


def call(tool, **args):
    async def go():
        async with Client(server.mcp) as cl:
            return await cl.call_tool(tool, args, raise_on_error=False)
    return asyncio.run(go())


def text(res):
    return res.content[0].text


# ---------------------------------------------------------------- accounts

def test_accounts_paginated_and_active_default(fake):
    out = text(call('meta_ads_list_accounts'))
    lines = out.splitlines()
    assert lines[0] == 'id,name,currency,timezone,status,business_name'
    assert any(line.startswith('act_1,') for line in lines)
    assert any(line.startswith('act_3,') for line in lines)  # page 2 reached
    assert 'act_2' not in out  # CLOSED account hidden by default


def test_accounts_all_and_name_filter(fake):
    out = text(call('meta_ads_list_accounts', status='ALL'))
    assert 'act_2' in out and 'CLOSED' in out
    out = text(call('meta_ads_list_accounts', status='ALL', name_contains='beta'))
    assert 'act_2' in out and 'act_1' not in out


def test_no_structured_duplicate(fake):
    res = call('meta_ads_list_accounts')
    assert res.structured_content is None


# ---------------------------------------------------------------- campaigns

def test_campaigns_compact_shape(fake):
    import csv as csv_mod
    import io
    out = text(call('meta_ads_list_campaigns', account_id='1'))
    rows = list(csv_mod.DictReader(io.StringIO(out)))
    assert len(rows) == 2
    first, second = rows
    assert first['id'] == '101' and first['name'] == 'Leads CH'
    assert first['daily_budget'] == '500'  # cents converted to currency units
    assert first['created'] == '2026-01-05' and first['updated'] == '2026-02-01'
    assert second['lifetime_budget'] == '1234.56'
    assert 'effective_status' not in rows[0] and first['status'] == 'ACTIVE'


def test_campaigns_default_status_and_act_prefix(fake):
    call('meta_ads_list_campaigns', account_id='1')
    endpoint, params = fake.calls[0]
    assert endpoint == '/act_1/campaigns'
    assert json.loads(params['effective_status']) == ['ACTIVE']


def test_campaigns_status_all_sends_no_filter(fake):
    call('meta_ads_list_campaigns', account_id='act_1', status='ALL')
    params = fake.last_params('/campaigns')
    assert 'effective_status' not in params


def test_campaigns_objective_list(fake):
    call('meta_ads_list_campaigns', account_id='1',
         objective=['OUTCOME_TRAFFIC', 'LINK_CLICKS'])
    filtering = json.loads(fake.last_params('/campaigns')['filtering'])
    assert filtering == [{'field': 'objective', 'operator': 'IN',
                          'value': ['OUTCOME_TRAFFIC', 'LINK_CLICKS']}]


def test_campaigns_json_mode(fake):
    data = json.loads(text(call('meta_ads_list_campaigns', account_id='1', format='json')))
    assert data['count'] == 2
    assert data['campaigns'][0]['daily_budget'] == 500
    assert 'lifetime_budget' not in data['campaigns'][0]  # nulls stripped


# ---------------------------------------------------------------- adsets / ads

def test_adsets_flatten_promoted_object(fake):
    out = text(call('meta_ads_list_adsets', account_id='1'))
    header = out.splitlines()[0].split(',')
    assert 'pixel_id' in header and 'custom_event_type' in header
    assert '777' in out and 'LEAD' in out


def test_adsets_campaign_scope_endpoint(fake):
    call('meta_ads_list_adsets', account_id='1', campaign_id='101')
    assert fake.calls[0][0] == '/101/adsets'


def test_ads_default_lean_creative(fake):
    out = text(call('meta_ads_list_ads', account_id='1'))
    params = fake.last_params('/ads')
    assert 'creative{id}' in params['fields'] and 'body' not in params['fields']
    assert 'preview_link' in out.splitlines()[0]
    assert 'https://fb.me/abc' in out


def test_ads_include_copy_requests_text(fake):
    call('meta_ads_list_ads', account_id='1', include_copy=True)
    assert 'creative{id,title,body}' in fake.last_params('/ads')['fields']


# ---------------------------------------------------------------- creatives

def test_creatives_batched_and_extracted(fake):
    out = text(call('meta_ads_get_creatives', creative_ids=['401', '402']))
    assert fake.calls[0][0] == '/'
    assert fake.calls[0][1]['ids'] == '401,402'
    lines = out.splitlines()
    assert lines[0] == 'creatives=2'
    assert lines[1] == 'creative_id,element,text'
    body = '\n'.join(lines)
    assert '401,body,Primary text here' in body
    assert '401,title,Headline here' in body
    assert '401,slide1_headline,Slide one headline' in body
    assert '401,slide2_headline,Slide two headline' in body
    assert '401,cta,LEARN_MORE' in body
    assert '402,afs_body1,AFS body A' in body
    assert '402,afs_title1,AFS title A' in body
    # body and link_data.message are the same text and must not duplicate
    assert body.count('Primary text here') == 1


def test_creatives_cap(fake):
    res = call('meta_ads_get_creatives', creative_ids=[str(i) for i in range(51)])
    assert res.is_error and '50' in text(res)


# ---------------------------------------------------------------- insights

def test_insights_level_adds_columns_automatically(fake):
    call('meta_ads_get_insights', account_id='1', start_date='2026-01-01',
         end_date='2026-01-31', level='campaign')
    params = fake.last_params('/insights')
    fields = params['fields'].split(',')
    assert fields[:2] == ['campaign_id', 'campaign_name']
    assert params['level'] == 'campaign'
    assert json.loads(params['time_range']) == {'since': '2026-01-01', 'until': '2026-01-31'}


def test_insights_compact_output(fake):
    out = text(call('meta_ads_get_insights', account_id='1',
                    start_date='2026-01-01', end_date='2026-01-31', level='campaign'))
    lines = out.splitlines()
    assert lines[0] == 'account=act_1 level=campaign range=2026-01-01..2026-01-31 increment=total rows=2'
    assert lines[1].startswith('totals: ')
    assert 'spend=150.75' in lines[1]
    assert 'impressions=30000' in lines[1]
    assert 'ctr_pct=1.67' in lines[1]  # 500/30000*100
    assert 'cpc=0.3' in lines[1]  # 150.75/500
    assert 'reach=' not in lines[1]  # non-additive stays out of totals
    header = lines[2].split(',')
    assert header[0] == 'campaign_id' and header[1] == 'campaign_name'
    assert 'action_lead' in header
    assert 'conversion_fb_pixel_custom.all_leads' in header
    assert 'date_start' not in header and 'date_stop' not in header


def test_insights_numeric_types_in_json(fake):
    data = json.loads(text(call('meta_ads_get_insights', account_id='1',
                                start_date='2026-01-01', end_date='2026-01-31',
                                level='campaign', format='json')))
    row = data['rows'][0]
    assert row['impressions'] == 20000 and isinstance(row['impressions'], int)
    assert row['spend'] == 100.5
    assert row['action_lead'] == 12
    assert row['campaign_id'] == '101'  # ids stay strings
    assert data['rows'][1]['conversion_fb_pixel_custom.all_leads'] == 7


def test_insights_daily_renames_date(fake):
    out = text(call('meta_ads_get_insights', account_id='1',
                    start_date='2026-01-01', end_date='2026-01-31',
                    level='campaign', time_increment='day'))
    params = fake.last_params('/insights')
    assert params['time_increment'] == '1'
    header = out.splitlines()[2].split(',')
    assert 'date' in header and 'date_start' not in header


def test_insights_action_type_allowlist(fake):
    out = text(call('meta_ads_get_insights', account_id='1',
                    start_date='2026-01-01', end_date='2026-01-31',
                    level='campaign', action_types=['lead']))
    assert 'action_lead' in out and 'action_link_click' not in out


def test_insights_id_filters(fake):
    call('meta_ads_get_insights', account_id='1', start_date='2026-01-01',
         end_date='2026-01-31', level='campaign', campaign_ids=['101'])
    filtering = json.loads(fake.last_params('/insights')['filtering'])
    assert filtering == [{'field': 'campaign.id', 'operator': 'IN', 'value': ['101']}]


def test_insights_bad_range_is_error(fake):
    res = call('meta_ads_get_insights', account_id='1',
               start_date='2026-02-01', end_date='2026-01-01')
    assert res.is_error and 'before start_date' in text(res)


def test_insights_truncation(fake, monkeypatch):
    monkeypatch.setattr(server, 'MAX_INSIGHTS_ROWS', 1)
    out = text(call('meta_ads_get_insights', account_id='1',
                    start_date='2026-01-01', end_date='2026-01-31', level='campaign'))
    assert 'truncated: showing first 1 of 2 rows' in out
    assert 'totals:' in out  # totals still cover all rows
    assert 'Sales FR' not in out


# ---------------------------------------------------------------- pixels

def test_pixels_slim(fake):
    out = text(call('meta_ads_list_pixels', account_id='1'))
    header = out.splitlines()[0]
    assert header.startswith('id,name,last_fired_time')
    assert 'can_proxy' not in header and 'creation_time' not in header


def test_pixel_stats_day_rollup(fake):
    out = text(call('meta_ads_get_pixel_stats', pixel_id='777',
                    start_date='2026-04-14', end_date='2026-04-15'))
    lines = out.splitlines()
    assert lines[0] == 'pixel=777 aggregation=event range=2026-04-14..2026-04-15 bucket=day rows=3'
    assert 'date,event,count' in lines[1]
    assert '2026-04-14,Lead,8' in out  # 5 + 3 summed across hours
    assert '2026-04-14,PageView,90' in out
    assert '2026-04-15,Lead,2' in out


def test_pixel_stats_total_rollup_and_window(fake):
    out = text(call('meta_ads_get_pixel_stats', pixel_id='777',
                    start_date='2026-04-14', end_date='2026-04-15', bucket='total'))
    assert 'Lead,10' in out
    params = fake.last_params('/777/stats')
    # end date is inclusive: epoch of the day after end_date
    assert int(params['end_time']) - int(params['start_time']) == 2 * 86400


def test_pixel_stats_value_is_label_not_number(fake):
    # Regression: `value` holds the event name, the number lives in `count`.
    # The old code coerced 'PageView' to float, fell back to 0 and dropped
    # the label, so every row read "count 0" with no event column.
    out = text(call('meta_ads_get_pixel_stats', pixel_id='777',
                    start_date='2026-04-14', end_date='2026-04-15'))
    lines = out.splitlines()
    assert '2026-04-14,PageView,90' in lines
    assert '2026-04-14,Lead,8' in lines
    assert not any(line.endswith(',0') for line in lines)


def test_pixel_stats_dimensionless_aggregation_number_in_value(fake):
    # Aggregations without a dimension (e.g. pixel_fire) return the number
    # in `value` with no `count`; it must still sum as the count.
    fake.overrides['/777/stats'] = {'data': [
        {'start_time': '2026-04-14T03:00:00+0000', 'data': [{'value': 4}]},
        {'start_time': '2026-04-14T09:00:00+0000', 'data': [{'value': 6}]},
    ]}
    out = text(call('meta_ads_get_pixel_stats', pixel_id='777',
                    start_date='2026-04-14', end_date='2026-04-14',
                    aggregation='pixel_fire'))
    assert '2026-04-14,10' in out


def test_pixel_stats_legacy_number_in_value_with_named_dim(fake):
    # Older stats shape: the number in `value` next to a named dimension key.
    fake.overrides['/777/stats'] = {'data': [
        {'start_time': '2026-04-14T03:00:00+0000',
         'data': [{'value': '5', 'event': 'Lead'}]},
    ]}
    out = text(call('meta_ads_get_pixel_stats', pixel_id='777',
                    start_date='2026-04-14', end_date='2026-04-14'))
    assert '2026-04-14,Lead,5' in out


def test_custom_conversion_stats_rollup(fake):
    out = text(call('meta_ads_get_custom_conversion_stats', custom_conversion_id='501',
                    start_date='2026-04-14', end_date='2026-04-14'))
    assert '2026-04-14,10' in out  # 4 + 6 via the count key and timestamp field


def test_custom_conversion_stats_dimensional_aggregation(fake):
    # Dimensional aggregations pair value (label) with count, like pixel
    # stats; the column must carry the aggregation name, not 'value'.
    fake.overrides['/501/stats'] = {'data': [
        {'timestamp': '2026-04-14T03:00:00+0000',
         'data': [{'value': 'desktop', 'count': 7}, {'value': 'mobile', 'count': 2}]},
    ]}
    out = text(call('meta_ads_get_custom_conversion_stats', custom_conversion_id='501',
                    start_date='2026-04-14', end_date='2026-04-14',
                    aggregation='device_type'))
    assert 'date,device_type,count' in out
    assert '2026-04-14,desktop,7' in out
    assert '2026-04-14,mobile,2' in out


# ---------------------------------------------------------------- pixel health

def test_pixel_health_compact(fake):
    out = text(call('meta_ads_get_pixel_health', pixel_id='777'))
    lines = out.splitlines()
    assert lines[0].startswith('pixel=777 name=Main Pixel')
    assert 'checks: PASS=1 FAIL=1' in out
    assert 'FAIL,event_coverage,Missing Purchase event' in out
    assert 'Pixel fired recently' not in out  # PASS details omitted


def test_pixel_health_degrades_gracefully(fake):
    fake.fail = FacebookAPIError('needs business_management', code=200)
    out = text(call('meta_ads_get_pixel_health', pixel_id='777'))
    assert 'da_checks_error:' in out and 'Permission denied' in out
    assert 'checks:' not in out or 'FAIL' not in out.splitlines()[0]


# ---------------------------------------------------------------- custom conversions

def test_custom_conversions_defaults(fake):
    out = text(call('meta_ads_list_custom_conversions', account_id='1'))
    assert 'All Leads' in out
    assert 'Old Conversion' not in out  # archived hidden by default
    assert 'rule' not in out.splitlines()[0]


def test_custom_conversions_opt_ins(fake):
    out = text(call('meta_ads_list_custom_conversions', account_id='1',
                    include_archived=True, include_rule=True))
    assert 'Old Conversion' in out
    assert 'rule' in out.splitlines()[0]


# ---------------------------------------------------------------- errors / retry

def test_token_error_is_actionable(fake):
    fake.fail = FacebookAPIError('Session has expired', code=190)
    res = call('meta_ads_list_campaigns', account_id='1')
    assert res.is_error
    assert 'code 190' in text(res) and 'FACEBOOK_ACCESS_TOKEN' in text(res)


def test_code_100_hint(fake):
    fake.fail = FacebookAPIError('Unsupported filter', code=100)
    res = call('meta_ads_list_campaigns', account_id='1')
    assert res.is_error and 'CONTAIN' in text(res)


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)

    @property
    def ok(self):
        return self.status_code < 400

    def json(self):
        return self._payload


def test_client_retries_transient_errors(monkeypatch):
    monkeypatch.setattr(facebook_client, 'RETRY_WAITS', (0, 0))
    responses = [
        FakeResponse(500, {'error': {'message': 'server error', 'code': 2}}),
        FakeResponse(429, {'error': {'message': 'rate limit', 'code': 17}}),
        FakeResponse(200, {'data': [{'id': '1'}]}),
    ]
    monkeypatch.setattr(facebook_client.requests, 'get',
                        lambda *a, **k: responses.pop(0))
    c = FacebookAdsClient(access_token='t')
    assert c._make_request('/x')['data'] == [{'id': '1'}]
    assert not responses


def test_client_does_not_retry_permanent_errors(monkeypatch):
    monkeypatch.setattr(facebook_client, 'RETRY_WAITS', (0, 0))
    calls = []

    def get(*a, **k):
        calls.append(1)
        return FakeResponse(400, {'error': {'message': 'bad field', 'code': 100}})

    monkeypatch.setattr(facebook_client.requests, 'get', get)
    c = FacebookAdsClient(access_token='t')
    with pytest.raises(FacebookAPIError) as exc:
        c._make_request('/x')
    assert exc.value.code == 100
    assert len(calls) == 1


def test_network_error_does_not_leak_token(monkeypatch):
    monkeypatch.setattr(facebook_client, 'RETRY_WAITS', ())

    def get(*a, **k):
        raise facebook_client.requests.exceptions.ConnectionError(
            'Max retries exceeded with url: /v26.0/act_1/campaigns'
            '?access_token=SECRET_TOKEN_VALUE&fields=id')

    monkeypatch.setattr(facebook_client.requests, 'get', get)
    c = FacebookAdsClient(access_token='SECRET_TOKEN_VALUE')
    with pytest.raises(FacebookAPIError) as exc:
        c._make_request('/x')
    assert 'SECRET_TOKEN_VALUE' not in str(exc.value)
    assert 'access_token=REDACTED' in str(exc.value)


def test_retry_exhaustion_attempt_count(monkeypatch):
    monkeypatch.setattr(facebook_client, 'RETRY_WAITS', (0, 0))
    calls = []

    def get(*a, **k):
        calls.append(1)
        return FakeResponse(500, {'error': {'message': 'down', 'code': 2}})

    monkeypatch.setattr(facebook_client.requests, 'get', get)
    c = FacebookAdsClient(access_token='t')
    with pytest.raises(FacebookAPIError) as exc:
        c._make_request('/x')
    assert len(calls) == 3  # initial attempt + one per retry wait
    assert exc.value.code == 2 and exc.value.http_status == 500


def test_totals_exclude_unique_and_rate_metrics(fake):
    fake.overrides['/insights'] = {'data': [
        {'spend': '10', 'unique_clicks': '100', 'estimated_ad_recall_rate': '1.5',
         'date_start': '2026-01-01', 'date_stop': '2026-01-31'},
        {'spend': '20', 'unique_clicks': '50', 'estimated_ad_recall_rate': '2.5',
         'date_start': '2026-01-01', 'date_stop': '2026-01-31'},
    ]}
    out = text(call('meta_ads_get_insights', account_id='1',
                    start_date='2026-01-01', end_date='2026-01-31',
                    metrics=['spend', 'unique_clicks', 'estimated_ad_recall_rate']))
    totals_line = [l for l in out.splitlines() if l.startswith('totals:')][0]
    assert 'spend=30' in totals_line
    assert 'unique_clicks' not in totals_line
    assert 'estimated_ad_recall_rate' not in totals_line


def test_single_row_still_gets_totals(fake):
    fake.overrides['/insights'] = {'data': [
        {'spend': '10.5', 'impressions': '1000', 'inline_link_clicks': '20',
         'date_start': '2026-01-01', 'date_stop': '2026-01-31'},
    ]}
    out = text(call('meta_ads_get_insights', account_id='1',
                    start_date='2026-01-01', end_date='2026-01-31'))
    assert 'totals: ' in out and 'ctr_pct=2' in out and 'cpc=0.53' in out


def test_pagination_preserves_params_on_next_page(fake):
    page2_params = {}

    def handler(params):
        if params.get('after'):
            page2_params.update(params)
            return {'data': [{'id': '103', 'name': 'Page Two',
                              'effective_status': 'ACTIVE'}]}
        return {
            'data': CAMPAIGNS['data'],
            'paging': {'next': 'https://graph.facebook.com/v26.0/act_1/campaigns'
                               '?after=c2&fields=id%2Cname&limit=100'
                               '&effective_status=%5B%22ACTIVE%22%5D'
                               '&filtering=%5B%7B%22field%22%3A%22name%22%2C%22operator'
                               '%22%3A%22CONTAIN%22%2C%22value%22%3A%22CH%22%7D%5D'},
        }

    fake.overrides['/campaigns'] = handler
    out = text(call('meta_ads_list_campaigns', account_id='1', name_contains='CH'))
    assert 'Page Two' in out
    assert page2_params['after'] == 'c2'
    assert json.loads(page2_params['effective_status']) == ['ACTIVE']
    assert json.loads(page2_params['filtering'])[0]['operator'] == 'CONTAIN'


def test_creative_text_with_commas_newlines_separator(fake):
    import csv as csv_mod
    import io
    tricky = 'a,b\nc ¦ d'
    fake.overrides['/'] = {'900': {'id': '900', 'body': tricky, 'title': 'T'}}
    out = text(call('meta_ads_get_creatives', creative_ids=['900']))
    rows = list(csv_mod.reader(io.StringIO('\n'.join(out.splitlines()[1:]))))
    assert rows[0] == ['creative_id', 'element', 'text']
    body_row = [r for r in rows[1:] if r[1] == 'body'][0]
    assert body_row[2] == tricky  # exact round-trip through CSV quoting


def test_numeric_ids_are_coerced(fake):
    res = call('meta_ads_list_campaigns', account_id=123456)
    assert not res.is_error
    assert fake.calls[0][0] == '/act_123456/campaigns'
    res = call('meta_ads_get_insights', account_id=1, start_date='2026-01-01',
               end_date='2026-01-31', campaign_ids='101')
    assert not res.is_error
    filtering = json.loads(fake.last_params('/insights')['filtering'])
    assert filtering[0]['value'] == ['101']


def test_paginated_page_guard():
    c = FacebookAdsClient(access_token='t')
    page = {'data': [{'id': '1'}],
            'paging': {'next': 'https://graph.facebook.com/v26.0/x?after=c'}}
    c._make_request = lambda endpoint, params=None: page
    result = c._make_paginated_request('/x', max_pages=3)
    assert len(result['data']) == 3
    assert result['pages_truncated'] is True

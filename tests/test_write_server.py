"""Offline write-server tests: a fake Graph API with GET + POST stands in for Meta.

Covers the full human-in-the-loop contract: preview diffs, unit conversion
(cents vs no-cent currencies), factor and cap guards, budget-type and CBO
blocks, end-date validation, token integrity and expiry, the password gate,
optimistic locking (stale rows), and post-write verification.
"""
import asyncio
import copy
import logging
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone

import pyotp
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import write_server  # noqa: E402
from facebook_client import FacebookAdsClient, FacebookAPIError  # noqa: E402
from fastmcp import Client  # noqa: E402

NOW = datetime.now(timezone.utc)
FUT = (NOW + timedelta(days=20)).strftime('%Y-%m-%dT23:00:00+0100')
FUT2 = (NOW + timedelta(days=40)).strftime('%Y-%m-%d')
PAST = (NOW - timedelta(days=5)).strftime('%Y-%m-%d')

ACCOUNTS = {
    '42': {'currency': 'CHF', 'timezone_name': 'Europe/Zurich'},
    '43': {'currency': 'JPY', 'timezone_name': 'Asia/Tokyo'},
}

ENTITIES = {
    # CBO campaign: lifetime budget + stop_time, one ad set without budget.
    '901': {'id': '901', 'name': 'CBO Lifetime', 'effective_status': 'ACTIVE',
            'lifetime_budget': '51500', 'stop_time': FUT,
            'start_time': '2026-10-01T00:00:00+0200', 'account_id': '42'},
    # Daily-budget campaign, no end date.
    '902': {'id': '902', 'name': 'Daily Camp', 'effective_status': 'ACTIVE',
            'daily_budget': '5000', 'account_id': '42'},
    # ABO campaign: no campaign-level budget, has an end date.
    '903': {'id': '903', 'name': 'ABO Camp', 'effective_status': 'ACTIVE',
            'stop_time': FUT, 'account_id': '42'},
    # Archived campaign.
    '904': {'id': '904', 'name': 'Old Camp', 'effective_status': 'ARCHIVED',
            'daily_budget': '1000', 'account_id': '42'},
    # JPY daily campaign (offset-1 currency).
    '921': {'id': '921', 'name': 'JP Camp', 'effective_status': 'ACTIVE',
            'daily_budget': '5000', 'account_id': '43'},
    # Ad set with its own daily budget (under ABO campaign 903).
    '911': {'id': '911', 'name': 'AS Daily', 'effective_status': 'ACTIVE',
            'daily_budget': '2500', 'account_id': '42', 'campaign_id': '903'},
    # Ad set under the CBO campaign: no own budget.
    '912': {'id': '912', 'name': 'AS CBO', 'effective_status': 'ACTIVE',
            'account_id': '42', 'campaign_id': '901'},
    # Ad set with lifetime budget and own end_time.
    '913': {'id': '913', 'name': 'AS Lifetime', 'effective_status': 'ACTIVE',
            'lifetime_budget': '80000', 'end_time': FUT,
            'start_time': '2026-10-01T00:00:00+0200',
            'account_id': '42', 'campaign_id': '903'},
}


class FakeGraph:
    """Stands in for FacebookAdsClient._make_request with GET + POST support."""

    def __init__(self):
        self.entities = copy.deepcopy(ENTITIES)
        self.posts = []
        self.calls = []
        self.fail_post = None

    def __call__(self, endpoint, params=None, method='GET', data=None):
        params = dict(params or {})
        self.calls.append((endpoint, params, method))
        obj_id = endpoint.strip('/')
        if method == 'POST':
            if self.fail_post:
                err, self.fail_post = self.fail_post, None
                raise err
            self.posts.append((obj_id, dict(data or {})))
            entity = self.entities[obj_id]
            for k, v in (data or {}).items():
                entity[k] = v
            return {'success': True}
        if obj_id.startswith('act_'):
            return dict(ACCOUNTS[obj_id.removeprefix('act_')])
        if obj_id.endswith('/adsets'):
            cid = obj_id.split('/')[0]
            return {'data': [copy.deepcopy(e) for e in self.entities.values()
                             if e.get('campaign_id') == cid]}
        if obj_id not in self.entities:
            raise FacebookAPIError('Unsupported get request', code=803)
        entity = copy.deepcopy(self.entities[obj_id])
        fields = params.get('fields', '')
        if 'campaign{' in fields and entity.get('campaign_id'):
            parent = self.entities[entity['campaign_id']]
            entity['campaign'] = {k: parent[k] for k in
                                  ('id', 'daily_budget', 'lifetime_budget')
                                  if k in parent}
        return entity


@pytest.fixture
def fake(monkeypatch):
    monkeypatch.setenv('FACEBOOK_ACCESS_TOKEN_W', 'test-token')
    monkeypatch.setenv('META_WRITE_PASSWORD', 'hunter2')
    monkeypatch.delenv('META_WRITE_TOTP_SECRET', raising=False)
    monkeypatch.delenv('META_WRITE_TOTP_SECRETS', raising=False)
    monkeypatch.setattr(write_server, 'AUTH_FAILURE_DELAY_SECONDS', 0)
    write_server._used_codes.clear()
    f = FakeGraph()
    c = FacebookAdsClient(access_token='test-token')
    c._make_request = f
    monkeypatch.setattr(write_server, '_client', c)
    return f


@pytest.fixture
def totp(fake, monkeypatch):
    """Switch the fake server to TOTP mode; returns the shared secret."""
    secret = pyotp.random_base32()
    monkeypatch.setenv('META_WRITE_TOTP_SECRET', secret)
    monkeypatch.delenv('META_WRITE_PASSWORD')
    return secret


def call(tool, **args):
    async def go():
        async with Client(write_server.mcp) as cl:
            return await cl.call_tool(tool, args, raise_on_error=False)
    return asyncio.run(go())


def text(res):
    return res.content[0].text


def preview(*changes, format='compact'):
    return text(call('meta_ads_preview_changes', changes=list(changes), format=format))


def tokens_of(out):
    """Pull change_token values out of compact preview output."""
    import csv as csv_mod
    import io
    lines = out.splitlines()
    start = next(i for i, l in enumerate(lines) if l.startswith('entity,'))
    rows = list(csv_mod.DictReader(io.StringIO('\n'.join(lines[start:]))))
    return {r['id'] + ':' + r['field']: r['change_token'] for r in rows}, rows


# ---------------------------------------------------------------- units

def test_to_minor_conversions():
    assert write_server._to_minor(515.5, 'CHF') == 51550
    assert write_server._to_minor(600, 'EUR') == 60000
    assert write_server._to_minor(1500, 'JPY') == 1500
    with pytest.raises(ValueError):
        write_server._to_minor(50.555, 'CHF')   # sub-cent
    with pytest.raises(ValueError):
        write_server._to_minor(1500.5, 'JPY')   # JPY has no decimals
    with pytest.raises(ValueError):
        write_server._to_minor(-5, 'CHF')
    with pytest.raises(ValueError):
        write_server._to_minor(0, 'CHF')
    with pytest.raises(ValueError):
        write_server._to_minor(float('inf'), 'CHF')


def test_write_client_requires_w_token(monkeypatch):
    """The write server never falls back to the read/generic token names."""
    monkeypatch.delenv('FACEBOOK_ACCESS_TOKEN_W', raising=False)
    monkeypatch.setenv('FACEBOOK_ACCESS_TOKEN', 'read-token')
    with pytest.raises(ValueError, match='FACEBOOK_ACCESS_TOKEN_W'):
        FacebookAdsClient(token_env_vars=('FACEBOOK_ACCESS_TOKEN_W',))


def test_api_error_surfaces_meta_user_message(monkeypatch):
    """Meta's error_user_title/_msg (e.g. the exact minimum budget) must
    reach the operator instead of a bare 'Invalid parameter'."""
    class FakeResp:
        ok = False
        status_code = 400
        text = 'x'
        def json(self):
            return {'error': {'message': 'Invalid parameter', 'code': 100,
                              'error_subcode': 1885650,
                              'error_user_title': 'Budget too low',
                              'error_user_msg': 'Your budget must be at least CHF189.70.'}}
    monkeypatch.setattr('facebook_client.requests.post', lambda *a, **k: FakeResp())
    c = FacebookAdsClient(access_token='t')
    with pytest.raises(FacebookAPIError) as ei:
        c.update_object('123', {'lifetime_budget': '10100'})
    assert 'Budget too low' in ei.value.message
    assert 'CHF189.70' in ei.value.message
    assert ei.value.code == 100


def test_env_num_validation(monkeypatch):
    assert write_server._env_num('X_UNSET_VAR', 5.0, 1.0) == 5.0
    for junk in ('0', '-3', 'abc', 'inf'):
        monkeypatch.setenv('X_VAR', junk)
        assert write_server._env_num('X_VAR', 5.0, 1.0) == 5.0
    monkeypatch.setenv('X_VAR', '2.5')
    assert write_server._env_num('X_VAR', 5.0, 1.0) == 2.5


# ---------------------------------------------------------------- preview

def test_preview_budget_ready_and_conversion(fake):
    out = preview({'entity_type': 'campaign', 'entity_id': '901',
                   'field': 'lifetime_budget', 'new_value': 600})
    toks, rows = tokens_of(out)
    assert 'ready=1' in out
    r = rows[0]
    assert r['current'] == '515.00' and r['proposed'] == '600.00'
    assert r['api_value'] == '60000' and r['currency'] == 'CHF'
    assert r['result'] == 'ready' and toks['901:lifetime_budget']
    assert fake.posts == []  # preview never writes


def test_preview_blocks_tenfold_then_override(fake):
    out = preview({'entity_type': 'campaign', 'entity_id': '902',
                   'field': 'daily_budget', 'new_value': 500})  # 50 -> 500
    assert 'blocked=1' in out and 'override=true' in out
    _, rows = tokens_of(out)
    assert rows[0]['change_token'] == ''
    out = preview({'entity_type': 'campaign', 'entity_id': '902',
                   'field': 'daily_budget', 'new_value': 500, 'override': True})
    assert 'ready=1' in out and 'override accepted' in out


def test_preview_budget_type_mismatch_blocked(fake):
    out = preview({'entity_type': 'campaign', 'entity_id': '901',
                   'field': 'daily_budget', 'new_value': 100})
    assert 'blocked=1' in out
    assert 'lifetime budget' in out and 'pacing model' in out


def test_preview_cbo_adset_points_to_campaign(fake):
    out = preview({'entity_type': 'adset', 'entity_id': '912',
                   'field': 'daily_budget', 'new_value': 100})
    assert 'blocked=1' in out
    assert '901' in out and 'campaign' in out


def test_preview_abo_campaign_points_to_adsets(fake):
    out = preview({'entity_type': 'campaign', 'entity_id': '903',
                   'field': 'daily_budget', 'new_value': 100})
    assert 'blocked=1' in out and 'ad sets' in out


def test_preview_jpy_offset_one(fake):
    out = preview({'entity_type': 'campaign', 'entity_id': '921',
                   'field': 'daily_budget', 'new_value': 6000})
    _, rows = tokens_of(out)
    assert rows[0]['api_value'] == '6000'  # NOT multiplied by 100
    assert rows[0]['current'] == '5000'
    out = preview({'entity_type': 'campaign', 'entity_id': '921',
                   'field': 'daily_budget', 'new_value': 1500.5})
    assert 'blocked=1' in out and 'decimal' in out


def test_preview_end_date_never_added_where_none_exists(fake):
    out = preview({'entity_type': 'campaign', 'entity_id': '902',
                   'field': 'end_date', 'new_value': FUT2})
    assert 'blocked=1' in out and 'does not add one' in out


def test_preview_end_date_past_blocked(fake):
    out = preview({'entity_type': 'campaign', 'entity_id': '901',
                   'field': 'end_date', 'new_value': PAST})
    assert 'blocked=1' in out and 'past' in out


def test_preview_end_date_extends_with_warnings(fake):
    out = preview({'entity_type': 'adset', 'entity_id': '913',
                   'field': 'end_date', 'new_value': FUT2})
    toks, rows = tokens_of(out)
    r = rows[0]
    assert r['result'] == 'ready' and toks['913:end_date']
    assert 're-paces' in r['warnings']          # lifetime budget warning
    assert r['api_value'].startswith(FUT2)      # account-tz ISO value
    assert '+' in r['api_value']                # carries a UTC offset


def test_preview_campaign_end_date_warns_adset_end_times(fake):
    out = preview({'entity_type': 'campaign', 'entity_id': '903',
                   'field': 'end_date', 'new_value': FUT2})
    _, rows = tokens_of(out)
    assert 'own end_time' in rows[0]['warnings']


def test_preview_archived_blocked(fake):
    out = preview({'entity_type': 'campaign', 'entity_id': '904',
                   'field': 'daily_budget', 'new_value': 20})
    assert 'blocked=1' in out and 'ARCHIVED' in out


def test_preview_no_change(fake):
    out = preview({'entity_type': 'campaign', 'entity_id': '901',
                   'field': 'lifetime_budget', 'new_value': 515})
    assert 'no_change=1' in out and 'ready=0' in out


def test_preview_unknown_entity_blocked(fake):
    out = preview({'entity_type': 'campaign', 'entity_id': '999',
                   'field': 'daily_budget', 'new_value': 20})
    assert 'blocked=1' in out


def test_preview_duplicate_rejected(fake):
    res = call('meta_ads_preview_changes', changes=[
        {'entity_type': 'campaign', 'entity_id': '902',
         'field': 'daily_budget', 'new_value': 60},
        {'entity_type': 'campaign', 'entity_id': '902',
         'field': 'daily_budget', 'new_value': 70},
    ])
    assert res.is_error and 'duplicate' in text(res)


def test_preview_batch_cap(fake):
    changes = [{'entity_type': 'campaign', 'entity_id': str(i),
                'field': 'daily_budget', 'new_value': 10} for i in range(51)]
    res = call('meta_ads_preview_changes', changes=changes)
    assert res.is_error and 'capped at 50' in text(res)


def test_preview_guard_scale_for_offset_one_currency(fake):
    """JPY 2,000,000/day is a normal budget, not a typo: unit guards scale
    x100 for offset-1 currencies, so with override this must pass."""
    out = preview({'entity_type': 'campaign', 'entity_id': '921',
                   'field': 'daily_budget', 'new_value': 2_000_000,
                   'override': True})
    assert 'ready=1' in out
    _, rows = tokens_of(out)
    assert rows[0]['api_value'] == '2000000'


def test_preview_without_password_is_dry_run(fake, monkeypatch):
    monkeypatch.delenv('META_WRITE_PASSWORD')
    out = preview({'entity_type': 'campaign', 'entity_id': '901',
                   'field': 'lifetime_budget', 'new_value': 600})
    assert 'ready=1' in out and 'dry run' in out
    _, rows = tokens_of(out)
    assert rows[0]['change_token'] == ''


def test_preview_campaign_end_blocked_when_all_adsets_have_ends(fake):
    """With EVERY ad set carrying its own end_time the campaign's stop_time
    is derived and a direct write is a verified no-op, so the preview blocks
    and redirects to the ad set ids. The earliest end is computed by instant,
    not string order: +0200 02:30 is earlier than +0100 02:00."""
    fake.entities['911']['end_time'] = '2026-12-20T02:30:00+0200'
    fake.entities['913']['end_time'] = '2026-12-20T02:00:00+0100'
    out = preview({'entity_type': 'campaign', 'entity_id': '903',
                   'field': 'end_date', 'new_value': FUT2})
    _, rows = tokens_of(out)
    assert 'blocked=1' in out
    result = rows[0]['result']
    assert 'earliest 2026-12-20T02:30' in result
    assert '911' in result and '913' in result    # redirect names the ad sets
    assert rows[0]['change_token'] == ''


def test_preview_advisory_adset_check_never_blocks(fake):
    """A broken ad set end_time (naive vs aware mix breaks sorting) degrades
    to a warning; the campaign end-date change itself stays ready."""
    fake.entities['911']['end_time'] = '2026-12-20T02:00:00'       # naive
    fake.entities['913']['end_time'] = '2026-12-20T02:00:00+0100'  # aware
    out = preview({'entity_type': 'campaign', 'entity_id': '903',
                   'field': 'end_date', 'new_value': FUT2})
    assert 'ready=1' in out


def test_preview_unparseable_existing_end_blocked(fake):
    fake.entities['901']['stop_time'] = 'not-a-date'
    out = preview({'entity_type': 'campaign', 'entity_id': '901',
                   'field': 'end_date', 'new_value': FUT2})
    assert 'blocked=1' in out and 'could not parse' in out


def test_preview_hard_ceiling_even_with_override(fake):
    out = preview({'entity_type': 'campaign', 'entity_id': '902',
                   'field': 'daily_budget', 'new_value': 2_000_000,
                   'override': True})
    assert 'blocked=1' in out and 'hard ceiling' in out


# ---------------------------------------------------------------- apply

def apply(tokens, approval='hunter2'):
    return call('meta_ads_apply_changes', change_tokens=tokens,
                approval_code=approval)


def test_apply_without_configured_secret(fake, monkeypatch):
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '901',
                                 'field': 'lifetime_budget', 'new_value': 600}))
    monkeypatch.delenv('META_WRITE_PASSWORD')
    res = apply(list(toks.values()), approval='anything')
    assert res.is_error and 'disabled' in text(res)
    assert fake.posts == []


def test_apply_wrong_password(fake):
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '901',
                                 'field': 'lifetime_budget', 'new_value': 600}))
    res = apply(list(toks.values()), approval='wrong')
    assert res.is_error and 'incorrect write password' in text(res)
    assert fake.posts == []


# ---------------------------------------------------------------- TOTP

def test_totp_apply_happy_path(totp, fake):
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '901',
                                 'field': 'lifetime_budget', 'new_value': 600}))
    out = text(apply(list(toks.values()), approval=pyotp.TOTP(totp).now()))
    assert 'applied=1' in out
    assert fake.entities['901']['lifetime_budget'] == '60000'


def test_totp_code_is_single_use(totp, fake):
    code = pyotp.TOTP(totp).now()
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '901',
                                 'field': 'lifetime_budget', 'new_value': 600}))
    assert 'applied=1' in text(apply(list(toks.values()), approval=code))
    toks2, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '902',
                                  'field': 'daily_budget', 'new_value': 60}))
    res = apply(list(toks2.values()), approval=code)
    assert res.is_error and 'already used' in text(res)
    assert fake.entities['902']['daily_budget'] == '5000'  # untouched


def test_totp_invalid_code(totp, fake):
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '901',
                                 'field': 'lifetime_budget', 'new_value': 600}))
    res = apply(list(toks.values()), approval='12345')
    assert res.is_error and 'invalid one-time code' in text(res)
    assert fake.posts == []


def test_totp_multi_operator_attribution(fake, monkeypatch):
    s1, s2 = pyotp.random_base32(), pyotp.random_base32()
    monkeypatch.delenv('META_WRITE_PASSWORD')
    monkeypatch.setenv('META_WRITE_TOTP_SECRETS', f'alice:{s1}, bob:{s2}')
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '901',
                                 'field': 'lifetime_budget', 'new_value': 600}))
    out = text(apply(list(toks.values()), approval=pyotp.TOTP(s2).now()))
    assert 'applied=1' in out and 'approved_by=bob' in out


def test_totp_multi_operator_replay_rejected(fake, monkeypatch):
    s1 = pyotp.random_base32()
    monkeypatch.delenv('META_WRITE_PASSWORD')
    monkeypatch.setenv('META_WRITE_TOTP_SECRETS', f'alice:{s1}')
    code = pyotp.TOTP(s1).now()
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '901',
                                 'field': 'lifetime_budget', 'new_value': 600}))
    assert 'approved_by=alice' in text(apply(list(toks.values()), approval=code))
    toks2, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '902',
                                  'field': 'daily_budget', 'new_value': 60}))
    res = apply(list(toks2.values()), approval=code)
    assert res.is_error and 'already used' in text(res)


def test_totp_code_survives_rejected_batch(totp, fake):
    """Batch validation happens before auth, so a bad call never burns the
    operator's single-use code."""
    code = pyotp.TOTP(totp).now()
    res = apply([], approval=code)
    assert res.is_error and 'at least one token' in text(res)
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '901',
                                 'field': 'lifetime_budget', 'new_value': 600}))
    assert 'applied=1' in text(apply(list(toks.values()), approval=code))


def test_preview_note_names_code_in_totp_mode(totp, fake):
    out = preview({'entity_type': 'campaign', 'entity_id': '901',
                   'field': 'lifetime_budget', 'new_value': 600})
    assert 'one-time code' in out and 'write password' not in out


def test_audit_logger_emits_info():
    import logging
    assert write_server.logger.handlers
    assert write_server.logger.level <= logging.INFO


def test_totp_misconfigured_secret_names_the_problem(fake, monkeypatch):
    """Pairs pasted into the SINGLE-secret var (the real-world mistake) must
    say 'misconfigured', not 'invalid one-time code'."""
    monkeypatch.delenv('META_WRITE_PASSWORD')
    monkeypatch.setenv('META_WRITE_TOTP_SECRET', 'alice:LOWER,bob:case')
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '901',
                                 'field': 'lifetime_budget', 'new_value': 600}))
    res = apply(list(toks.values()), approval='123456')
    assert res.is_error and 'misconfigured' in text(res)
    assert 'META_WRITE_TOTP_SECRETS' in text(res)


def test_totp_rejects_static_password(totp, fake):
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '901',
                                 'field': 'lifetime_budget', 'new_value': 600}))
    res = apply(list(toks.values()), approval='hunter2')
    assert res.is_error and 'invalid one-time code' in text(res)
    assert fake.posts == []


def test_apply_happy_path_budget(fake):
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '901',
                                 'field': 'lifetime_budget', 'new_value': 600}))
    out = text(apply(list(toks.values())))
    assert 'applied=1' in out
    assert fake.posts == [('901', {'lifetime_budget': '60000'})]
    assert fake.entities['901']['lifetime_budget'] == '60000'
    lines = out.splitlines()
    assert any('515.00' in l and '600.00' in l for l in lines)


def test_apply_end_date_params(fake):
    toks, _ = tokens_of(preview(
        {'entity_type': 'adset', 'entity_id': '913',
         'field': 'end_date', 'new_value': FUT2},
        {'entity_type': 'campaign', 'entity_id': '901',
         'field': 'end_date', 'new_value': FUT2},
    ))
    out = text(apply([toks['913:end_date'], toks['901:end_date']]))
    assert 'applied=2' in out
    posted = dict(fake.posts)
    assert 'end_time' in posted['913'] and 'stop_time' in posted['901']
    assert posted['913']['end_time'].startswith(FUT2)


def test_apply_stale_row_refused(fake):
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '901',
                                 'field': 'lifetime_budget', 'new_value': 600}))
    fake.entities['901']['lifetime_budget'] = '52000'  # someone changed it
    out = text(apply(list(toks.values())))
    assert 'stale=1' in out and 're-run' in out
    assert fake.posts == []


def test_apply_already_applied_noop(fake):
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '901',
                                 'field': 'lifetime_budget', 'new_value': 600}))
    fake.entities['901']['lifetime_budget'] = '60000'
    out = text(apply(list(toks.values())))
    assert 'already_applied=1' in out
    assert fake.posts == []


def test_apply_idempotent_double_run(fake):
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '901',
                                 'field': 'lifetime_budget', 'new_value': 600}))
    assert 'applied=1' in text(apply(list(toks.values())))
    out = text(apply(list(toks.values())))
    assert 'already_applied=1' in out
    assert len(fake.posts) == 1  # only the first run wrote


def test_apply_tampered_token(fake):
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '901',
                                 'field': 'lifetime_budget', 'new_value': 600}))
    tok = list(toks.values())[0]
    bad = ('A' if tok[0] != 'A' else 'B') + tok[1:]
    out = text(apply([bad]))
    assert 'invalid=1' in out and fake.posts == []


def test_apply_expired_token(fake, monkeypatch):
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '901',
                                 'field': 'lifetime_budget', 'new_value': 600}))
    monkeypatch.setattr(write_server, 'TOKEN_TTL_SECONDS', -1)
    out = text(apply(list(toks.values())))
    assert 'invalid=1' in out and 'expired' in out and fake.posts == []


def test_apply_partial_failure_is_independent(fake):
    toks, _ = tokens_of(preview(
        {'entity_type': 'campaign', 'entity_id': '901',
         'field': 'lifetime_budget', 'new_value': 600},
        {'entity_type': 'campaign', 'entity_id': '902',
         'field': 'daily_budget', 'new_value': 60},
    ))
    fake.entities['901']['lifetime_budget'] = '52000'  # first goes stale
    out = text(apply([toks['901:lifetime_budget'], toks['902:daily_budget']]))
    assert 'stale=1' in out and 'applied=1' in out
    assert fake.entities['902']['daily_budget'] == '6000'


def test_apply_meta_error_mapped(fake):
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '902',
                                 'field': 'daily_budget', 'new_value': 60}))
    fake.fail_post = FacebookAPIError('Invalid parameter', code=100)
    out = text(apply(list(toks.values())))
    assert 'error=1' in out and 'code 100' in out
    assert fake.entities['902']['daily_budget'] == '5000'  # unchanged


def test_apply_row_isolation_on_malformed_payload(fake):
    """A validly signed token with a broken payload becomes an error row and
    never aborts the rest of the batch."""
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '902',
                                 'field': 'daily_budget', 'new_value': 60}))
    bad = write_server._make_token({'v': 1, 't': 'campaign', 'id': '902',
                                    'f': 'daily_budget', 'old': '5000',
                                    'cur': 'CHF', 'iat': int(time.time())})
    out = text(apply([bad] + list(toks.values())))
    assert 'error=1' in out and 'applied=1' in out
    assert fake.entities['902']['daily_budget'] == '6000'


def test_apply_adset_reread_is_lean(fake):
    """Apply re-reads never carry the nested campaign expansion."""
    toks, _ = tokens_of(preview({'entity_type': 'adset', 'entity_id': '911',
                                 'field': 'daily_budget', 'new_value': 30}))
    fake.calls.clear()
    out = text(apply(list(toks.values())))
    assert 'applied=1' in out
    adset_gets = [p for ep, p, m in fake.calls if ep == '/911' and m == 'GET']
    assert adset_gets
    assert all('campaign{' not in p.get('fields', '') for p in adset_gets)


def test_apply_verification_reads_back(fake):
    """Every applied row is re-read: 2 GETs + 1 POST per entity minimum."""
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '902',
                                 'field': 'daily_budget', 'new_value': 60}))
    fake.posts.clear()
    out = text(apply(list(toks.values())))
    assert 'applied=1' in out and len(fake.posts) == 1

# ---------------------------------------------------------------- audit logs

class _LogCapture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


def _capture():
    handler = _LogCapture()
    write_server.logger.addHandler(handler)
    return handler


def test_apply_logs_carry_audit_context(fake):
    """Applied rows log entity name, account, human-readable values and a
    batch id that the output header echoes, so a conversation and the
    deployment logs can always be tied together."""
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '901',
                                 'field': 'lifetime_budget', 'new_value': 600}))
    handler = _capture()
    try:
        out = text(apply(list(toks.values())))
    finally:
        write_server.logger.removeHandler(handler)
    joined = '\n'.join(handler.lines)
    assert '"CBO Lifetime"' in joined and 'act=42' in joined
    assert 'CHF 515.00 -> 600.00 (raw 51500 -> 60000)' in joined
    assert 'approved_by=password' in joined
    batch_id = re.search(r'batch=([0-9a-f]{8})', out).group(1)
    assert f'batch={batch_id}' in joined
    assert any(line.startswith(f'batch={batch_id} done') for line in handler.lines)


def test_preview_logs_summary_line(fake):
    handler = _capture()
    try:
        preview({'entity_type': 'campaign', 'entity_id': '901',
                 'field': 'lifetime_budget', 'new_value': 600})
    finally:
        write_server.logger.removeHandler(handler)
    assert any(line.startswith('preview n=1 ready=1 blocked=0 no_change=0')
               and 'accounts=42' in line for line in handler.lines)


def test_mcp_library_loggers_quieted():
    """The MCP plumbing must not bury the audit trail at INFO."""
    assert logging.getLogger('mcp').level >= logging.WARNING


# ---------------------------------------------------------------- totp window

def test_totp_leeway_accepts_previous_step_code(fake, totp):
    """A code stays valid ~60-90s after it appears: slow claude.ai round
    trips (human types, model composes, client confirms) must not expire it."""
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '902',
                                 'field': 'daily_budget', 'new_value': 60}))
    aged = pyotp.TOTP(totp).at(time.time() - 60)     # two 30s steps old
    assert 'applied=1' in text(apply(list(toks.values()), approval=aged))


def test_totp_rejects_three_step_old_code(fake, totp):
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '902',
                                 'field': 'daily_budget', 'new_value': 60}))
    ancient = pyotp.TOTP(totp).at(time.time() - 90)  # three steps: too old
    res = apply(list(toks.values()), approval=ancient)
    assert res.is_error and 'invalid one-time code' in text(res)


def test_apply_error_rows_are_logged(fake):
    """A row that fails against Meta must reach the audit trail with its
    reason, not just bump the error count in the output."""
    toks, _ = tokens_of(preview({'entity_type': 'campaign', 'entity_id': '902',
                                 'field': 'daily_budget', 'new_value': 60}))
    fake.fail_post = FacebookAPIError('Budget too low', code=100)
    handler = _capture()
    try:
        out = text(apply(list(toks.values())))
    finally:
        write_server.logger.removeHandler(handler)
    assert 'error=1' in out
    assert any(line.startswith('error campaign 902 daily_budget:')
               and 'batch=' in line for line in handler.lines)

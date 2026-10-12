"""Unit tests for the filter builder."""
import os
import sys
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from facebook_client import _build_list_filters, _date_to_unix


class TestDateToUnix:
    def test_known_date(self):
        # 2026-01-01 00:00:00 UTC == 1767225600
        assert _date_to_unix('2026-01-01') == 1767225600

    def test_epoch(self):
        assert _date_to_unix('1970-01-01') == 0

    def test_invalid_format_raises(self):
        with pytest.raises(ValueError):
            _date_to_unix('2026/01/01')

    def test_garbage_raises(self):
        with pytest.raises(ValueError):
            _date_to_unix('not-a-date')


class TestBuildListFilters:
    def test_no_args_returns_empty(self):
        assert _build_list_filters() == []

    def test_name_contains(self):
        assert _build_list_filters(name_contains='abc') == [
            {'field': 'name', 'operator': 'CONTAIN', 'value': 'abc'}
        ]

    def test_objective_uses_in_operator(self):
        # Meta rejects EQUAL on `objective`; only IN with a list works.
        # We accept a single string but compile to IN [value].
        assert _build_list_filters(objective='OUTCOME_LEADS') == [
            {'field': 'objective', 'operator': 'IN', 'value': ['OUTCOME_LEADS']}
        ]

    def test_objective_list_passes_through(self):
        assert _build_list_filters(objective=['OUTCOME_TRAFFIC', 'LINK_CLICKS']) == [
            {'field': 'objective', 'operator': 'IN', 'value': ['OUTCOME_TRAFFIC', 'LINK_CLICKS']}
        ]

    def test_created_after_converts_date(self):
        result = _build_list_filters(created_after='2026-01-01')
        assert result == [
            {'field': 'created_time', 'operator': 'GREATER_THAN', 'value': 1767225600}
        ]

    def test_created_before_converts_date(self):
        result = _build_list_filters(created_before='2026-01-01')
        assert result == [
            {'field': 'created_time', 'operator': 'LESS_THAN', 'value': 1767225600}
        ]

    def test_updated_after_converts_date(self):
        result = _build_list_filters(updated_after='2026-01-01')
        assert result == [
            {'field': 'updated_time', 'operator': 'GREATER_THAN', 'value': 1767225600}
        ]

    def test_multiple_filters_preserved_in_order(self):
        result = _build_list_filters(
            name_contains='abc',
            objective='OUTCOME_LEADS',
            created_after='2026-01-01',
        )
        assert result == [
            {'field': 'name', 'operator': 'CONTAIN', 'value': 'abc'},
            {'field': 'objective', 'operator': 'IN', 'value': ['OUTCOME_LEADS']},
            {'field': 'created_time', 'operator': 'GREATER_THAN', 'value': 1767225600},
        ]

    def test_extra_filters_passthrough(self):
        extra = [{'field': 'daily_budget', 'operator': 'GREATER_THAN', 'value': 5000}]
        assert _build_list_filters(extra_filters=extra) == extra

    def test_extra_filters_anded_with_convenience(self):
        extra = [{'field': 'daily_budget', 'operator': 'GREATER_THAN', 'value': 5000}]
        result = _build_list_filters(name_contains='abc', extra_filters=extra)
        assert result == [
            {'field': 'name', 'operator': 'CONTAIN', 'value': 'abc'},
            {'field': 'daily_budget', 'operator': 'GREATER_THAN', 'value': 5000},
        ]

    def test_invalid_date_in_filter_raises(self):
        with pytest.raises(ValueError):
            _build_list_filters(created_after='2026/01/01')

    def test_empty_string_name_treated_as_falsy(self):
        # Empty string is falsy and should not produce a filter row
        assert _build_list_filters(name_contains='') == []

    def test_date_range_window(self):
        result = _build_list_filters(
            created_after='2026-01-01',
            created_before='2026-02-01',
        )
        assert len(result) == 2
        assert result[0]['operator'] == 'GREATER_THAN'
        assert result[1]['operator'] == 'LESS_THAN'
        assert result[0]['value'] < result[1]['value']

import os
import sys
from tempfile import TemporaryDirectory
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from unittest.mock import Mock
import pytest
sys.path.insert(0, str(Path(__file__).parents[1]))

from src.contractor_matcher import Contractor, Request, explain, match
from src.contractor_matcher import load_catalog


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)


def c(id, **kw):
    defaults = dict(anon_name=id, categories="Ведущий", city="Алматы", city_imputed=False, synthetic=False,
                    price_from_kzt=100000, price_imputed=False, event_formats="корпоратив", languages="русский",
                    max_hours=8, busy_dates="", description="Вёл корпоратив для команды из 100 человек")
    defaults.update(kw); return Contractor(id=id, **defaults)


def request(**kw):
    d = dict(city="Алматы", date="2026-10-10", event_format="корпоратив", category="Ведущий", budget_kzt=300000)
    d.update(kw); return Request(**d)


def test_composite_category_and_stable_tie_break():
    result = match(request(), [c("2", categories="Ведущий|Модератор"), c("1", categories="Ведущий|Модератор")])
    assert result.status == "matched"
    assert [x["id"] for x in result.matches] == ["1", "2"]


def test_composite_event_format_passes_filter():
    result = match(request(event_format="конференция"), [c("1", event_formats="корпоратив|конференция")])
    assert result.status == "matched"
    assert [x["id"] for x in result.matches] == ["1"]


def test_match_returns_at_most_three_cards():
    result = match(request(), [c(str(i), price_from_kzt=100000 + i) for i in range(5)])
    assert result.status == "matched"
    assert len(result.matches) == 3


def test_no_category_is_explained():
    result = match(request(category="Флорист"), [c("1")])
    assert result.status == "no_category"
    assert "нет подрядчиков" in result.message


def test_no_category_and_no_match_messages_are_distinct():
    no_category = match(request(category="Флорист"), [c("1")])
    no_match = match(request(), [c("1", busy_dates="2026-10-10")])
    assert no_category.status == "no_category"
    assert no_match.status == "no_match"
    assert no_category.message != no_match.message


def test_no_match_reports_reasons():
    result = match(request(), [c("1", busy_dates="2026-10-10"), c("2", price_from_kzt=900000)])
    assert result.status == "no_match"
    assert result.excluded["busy"] == ["1"]
    assert result.excluded["budget"] == ["2"]


def test_different_dates_change_available_cards_for_busy_candidate():
    catalog = [c("1", busy_dates="2026-10-10"), c("2")]
    on_busy_date = match(request(date="2026-10-10"), catalog)
    on_free_date = match(request(date="2026-10-11"), catalog)
    assert [x["id"] for x in on_busy_date.matches] == ["2"]
    assert [x["id"] for x in on_free_date.matches] == ["1", "2"]


def test_explain_offline_fallback_is_short_and_uses_matched_criteria():
    candidate = {
        "id": "1", "anon_name": "Ведущий 1", "price_from_kzt": 100000,
        "description": "Провёл 200 корпоративов. Работал с крупными командами и площадками.",
        "matched_criteria": ["language", "duration"],
    }
    with TemporaryDirectory() as cache_dir:
        old_key = os.environ.pop("OPENAI_API_KEY", None)
        try:
            result = explain(candidate, request(language="английский", duration_hours=6), cache_dir)
        finally:
            if old_key is not None:
                os.environ["OPENAI_API_KEY"] = old_key
        assert "100 000" in result["reason"]
        assert "английский" in result["reason"]
        assert "6 ч" in result["reason"]
        assert len(result["reason"]) < 500


def test_explain_api_failure_returns_fallback_without_raising():
    class FailingResponses:
        def create(self, **kwargs):
            raise RuntimeError("network unavailable")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.responses = FailingResponses()

    candidate = {
        "id": "1", "anon_name": "Ведущий 1", "price_from_kzt": 100000,
        "description": "Провёл 200 корпоративов.", "matched_criteria": ["language"],
    }
    with TemporaryDirectory() as cache_dir, patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}), \
            patch.dict(sys.modules, {"openai": SimpleNamespace(OpenAI=FakeOpenAI)}):
        result = explain(candidate, request(language="английский"), cache_dir)
    assert "Ведущий 1" in result["reason"]
    assert "английский" in result["reason"]


def test_real_catalog_and_repeatability():
    catalog = load_catalog(Path(__file__).parents[1] / 'data/contractors.csv')
    assert len(catalog) == 66
    assert sum(c.synthetic for c in catalog) == 13
    query = request(budget_kzt=2000000)
    results = [match(query, catalog, offline=True) for _ in range(3)]
    assert [[m['id'] for m in r.matches] for r in results] == [['HK-72938', 'HK-88430', 'HK-75012']] * 3
    assert all(m['reason'] for m in results[0].matches)
    assert len({m['reason'] for m in results[0].matches}) == 3
    other = match(request(budget_kzt=2000000, date='2026-10-11'), catalog, offline=True)
    assert [m['id'] for m in other.matches] == ['HK-72938', 'HK-77838', 'HK-44923']
    assert 'HK-88430' in other.excluded['busy']
    assert 'занятости' in other.message


def test_null_duration_does_not_exclude():
    assert match(request(duration_hours=20), [c('1', max_hours=None)]).status == 'matched'


def test_successful_api_cache_and_corrupt_cache_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    client = Mock()
    client.responses.create.return_value.output_text = '```json\n{"reason":"Провёл 200 корпоративов."}\n```'
    factory = Mock(return_value=client)
    candidate = {'anon_name': '1', 'price_from_kzt': 100000, 'description': 'Провёл 200 корпоративов.'}
    with patch.dict(sys.modules, {'openai': SimpleNamespace(OpenAI=factory)}):
        first = explain(candidate, request(), tmp_path)
        assert explain(candidate, request(), tmp_path) == first
        assert client.responses.create.call_count == 1
        factory.assert_called_once_with(timeout=2.0, max_retries=0)
        next(tmp_path.glob('*.json')).write_text('broken', encoding='utf-8')
        assert explain(candidate, request(), tmp_path) == first
        assert client.responses.create.call_count == 2


def test_failure_not_cached_and_recovery_retries(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-key')
    client = Mock()
    client.responses.create.side_effect = [RuntimeError('network'), SimpleNamespace(output_text='{"reason":"Вёл 100 событий."}')]
    candidate = {'anon_name': '1', 'price_from_kzt': 100000, 'description': 'Вёл 100 событий.'}
    with patch.dict(sys.modules, {'openai': SimpleNamespace(OpenAI=Mock(return_value=client))}):
        assert '100 000' in explain(candidate, request(), tmp_path)['reason']
        assert not list(tmp_path.glob('*.json'))
        assert explain(candidate, request(), tmp_path)['reason'] == 'Вёл 100 событий.'
        assert client.responses.create.call_count == 2

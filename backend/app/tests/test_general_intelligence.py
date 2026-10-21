from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from backend.app.services.general_intelligence import (
    IntelligenceError,
    calculate,
    convert_currency,
    convert_unit,
    date_time_operation,
    geocode_location,
    get_holidays,
    get_route,
    get_weather,
    search_places,
)


@pytest.mark.parametrize(('expression', 'expected'), [
    ('17.5/100*860', '150.5'), ('18/100*425', '76.5'), ('2+3*4', '14'),
    ('(2+3)*4', '20'), ('10/4', '2.5'), ('2**10', '1024'), ('7%3', '1'),
    ('-12+5', '-7'), ('0.1+0.2', '0.3'), ('100-15/100*100', '85'),
])
def test_safe_decimal_calculator(expression, expected):
    assert calculate(expression)['value'] == expected


@pytest.mark.parametrize('expression', [
    '__import__("os")', 'open("x")', 'x+1', '2**100', '1/0', '[1,2,3]',
])
def test_calculator_rejects_code_and_unsafe_operations(expression):
    with pytest.raises(IntelligenceError):
        calculate(expression)


@pytest.mark.parametrize(('value', 'source', 'target', 'expected'), [
    (0, 'c', 'f', '32.000000'), (32, 'f', 'c', '0.000000'),
    (1, 'km', 'mi', '0.621371'), (1, 'mi', 'km', '1.609344'),
    (1, 'kg', 'lb', '2.204623'), (1, 'lb', 'kg', '0.453592'),
    (1, 'm', 'ft', '3.280840'), (1, 'gal_us', 'l', '3.785412'),
])
def test_deterministic_unit_conversions(value, source, target, expected):
    assert convert_unit(value, source, target)['result'] == expected


def test_date_time_operations_cover_dst_relative_dates_and_differences():
    winter = datetime(2026, 1, 15, 12, tzinfo=ZoneInfo('America/Halifax'))
    summer = datetime(2026, 7, 15, 12, tzinfo=ZoneInfo('America/Halifax'))
    assert date_time_operation('current', now=winter)['is_dst'] is False
    assert date_time_operation('current', now=summer)['is_dst'] is True
    assert date_time_operation('relative', relative='tomorrow', now=winter)['date'] == '2026-01-16'
    assert date_time_operation('relative', relative='next friday', now=winter)['date'] == '2026-01-16'
    assert date_time_operation('add_days', timestamp='2026-01-15T12:00:00-04:00', days=45)['date'] == '2026-03-01'
    difference = date_time_operation(
        'difference', timestamp='2026-01-01T00:00:00+00:00',
        other_timestamp='2026-01-03T12:00:00+00:00',
    )
    assert difference == {'operation': 'difference', 'seconds': '216000.0', 'days': '2.5'}
    converted = date_time_operation(
        'convert', timezone_name='Asia/Kolkata', source_timezone='America/Halifax',
        timestamp='2026-01-15T09:00:00',
    )
    assert converted['timestamp'].startswith('2026-01-15T18:30:00+05:30')


class Response:
    def __init__(self, value): self.value = value
    def raise_for_status(self): return None
    def json(self): return self.value


class Session:
    def __init__(self, *values): self.values, self.calls = list(values), []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return Response(self.values.pop(0))


PLACE = {'results': [{
    'name': 'Halifax', 'country': 'Canada', 'admin1': 'Nova Scotia',
    'latitude': 44.65, 'longitude': -63.57, 'timezone': 'America/Halifax',
}]}


def test_weather_preserves_observation_forecast_units_and_no_alert_claim():
    session = Session(PLACE, {
        'timezone': 'America/Halifax',
        'current': {'time': '2026-10-21T05:00', 'temperature_2m': 8.2, 'snowfall': 0},
        'current_units': {'temperature_2m': '°C', 'snowfall': 'cm'},
        'daily': {'time': ['2026-10-22'], 'temperature_2m_max': [10], 'snowfall_sum': [1.2]},
        'daily_units': {'temperature_2m_max': '°C', 'snowfall_sum': 'cm'},
    })
    result = get_weather('Halifax', 1, session=session)
    assert result['provider'] == 'Open-Meteo'
    assert result['observed_at'] == '2026-10-21T05:00'
    assert result['forecast'][0] == {'date': '2026-10-22', 'temperature_2m_max': 10, 'snowfall_sum': 1.2}
    assert result['alerts'] is None


def test_location_place_route_currency_and_holiday_provenance():
    geocode = Session(PLACE)
    assert geocode_location('Halifax', 1, geocode)[0]['timezone'] == 'America/Halifax'

    places = Session([{'display_name': 'Cafe, Halifax', 'lat': '44.6', 'lon': '-63.5', 'osm_type': 'node', 'osm_id': 7, 'category': 'amenity', 'type': 'cafe'}])
    assert search_places('restaurants', 'Halifax', 1, places)[0]['ratings_available'] is False

    route = Session(PLACE, {'results': [{**PLACE['results'][0], 'name': 'Moncton', 'longitude': -64.77}]}, {'code': 'Ok', 'routes': [{'distance': 263400, 'duration': 10140}]})
    routed = get_route('Halifax', 'Moncton', session=route)
    assert routed['distance_km'] == 263.4 and routed['duration_minutes'] == 169.0
    assert routed['traffic_included'] is False

    currency = Session({'date': '2026-10-20', 'rates': {'USD': 361.25}})
    converted = convert_currency(500, 'CAD', 'USD', session=currency)
    assert converted['converted_amount'] == '361.25' and converted['rate_date'] == '2026-10-20'

    holidays = Session([
        {'date': '2026-10-12', 'name': 'Thanksgiving', 'localName': 'Thanksgiving', 'countryCode': 'CA', 'counties': None, 'types': ['Public']},
        {'date': '2026-02-16', 'name': 'Heritage Day', 'localName': 'Heritage Day', 'countryCode': 'CA', 'counties': ['CA-NS'], 'types': ['Public']},
        {'date': '2026-02-16', 'name': 'Family Day', 'localName': 'Family Day', 'countryCode': 'CA', 'counties': ['CA-ON'], 'types': ['Public']},
    ])
    scoped = get_holidays('CA', 2026, 'CA-NS', holidays)
    assert [row['name'] for row in scoped] == ['Thanksgiving', 'Heritage Day']
    assert scoped[0]['national'] is True and scoped[1]['national'] is False


def test_general_tools_are_discovered_read_only_and_workflow_authorized(make_client):
    from backend.app.agent.tools import KNOWLEDGE_TOOLS, READ_TOOLS

    names = {
        'calculate_date_time', 'calculate', 'convert_unit', 'geocode_location',
        'get_weather', 'search_places', 'get_route', 'convert_currency', 'get_holidays',
    }
    assert names <= READ_TOOLS and names <= KNOWLEDGE_TOOLS
    client = make_client({'ENABLE_PUBLIC_WEB_TOOLS': 'true'})
    harness = client.app.state.agent.harness
    assert names <= harness.allowed_names('knowledge')
    for name in names:
        assert harness.descriptors[name]['annotations']['readOnlyHint'] is True


def test_frozen_general_intelligence_benchmark_has_40_varied_cases():
    cases = json.loads((Path(__file__).with_name('general_intelligence_cases.json')).read_text())
    assert len(cases) == 40
    assert len({case['category'] for case in cases}) >= 14
    assert sum(bool(case.get('clarification')) for case in cases) >= 4
    assert all(case['prompt'] and isinstance(case['tools'], list) for case in cases)


def test_failed_general_tool_signature_prevents_identical_retry(make_client):
    client = make_client()
    agent = client.app.state.agent

    async def unavailable(name, arguments):
        return {
            'tool': name, 'source_system': 'tool_error', 'status': 'error',
            'error': 'Explicit isolated outage', 'items': [], 'reliability': 0,
            'timestamp': '2026-10-21T00:00:00Z',
        }

    agent.harness.invoke = unavailable
    state = {
        'case_id': 'failed-tool', 'persist_case': False, 'workflow': 'knowledge',
        'ticket': {'summary': 'Calculate 2 + 2'}, 'research_rounds': 0,
        'plan': {
            'goal': 'Calculate safely', 'additional_evidence_useful': True,
            'tool_calls': [{'name': 'calculate', 'arguments': {'expression': '2+2'}, 'reason': 'Exact arithmetic'}],
            'stop_condition': 'An exact result is observed',
        },
    }
    update = client.portal.call(agent.execute_tools, state)
    signature = agent._tool_signature('calculate', {'expression': '2+2'})
    assert update['executed_tool_calls'] == [signature]
    assert update['tool_results'][0]['output']['status'] == 'error'


def test_external_general_observation_retains_public_source_provenance(make_client):
    agent = make_client().app.state.agent
    evidence = agent._evidence({'case_id': 'weather', 'persist_case': False}, 'get_weather', {
        'source_system': 'open_meteo', 'status': 'ok', 'reliability': .9,
        'timestamp': '2026-10-21T12:00:00Z',
        'items': [{
            'provider': 'Open-Meteo', 'source_url': 'https://open-meteo.com/en/docs',
            'observed_at': '2026-10-21T11:45:00Z', 'temperature': 8.2, 'unit': '°C',
        }],
    })
    assert evidence[0]['source_type'] == 'public_documentation'
    assert evidence[0]['url'] == 'https://open-meteo.com/en/docs'
    assert evidence[0]['observed_at'] == '2026-10-21T11:45:00Z'


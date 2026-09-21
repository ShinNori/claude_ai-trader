"""J-Quants transport parsing with fake sessions only."""
import copy
import traceback

import pytest
import requests

from aitrader.jquants import JQuantsClient


SECRET = 'secret-token-url-body-value'
REFRESH = 'sensitive-refresh-123'


class Response:
    def __init__(self, payload=None, status=200, error=None):
        self.payload = payload
        self.status_code = status
        self.error = error

    def json(self):
        if self.error:
            raise self.error
        return self.payload


class Session:
    def __init__(self, post=None, gets=()):
        self.post_response = post or Response({'idToken': 'id-token'})
        self.get_responses = list(gets)
        self.post_calls = []
        self.get_calls = []

    def post(self, url, **kwargs):
        self.post_calls.append((url, kwargs))
        if isinstance(self.post_response, BaseException):
            raise self.post_response
        return self.post_response

    def get(self, url, **kwargs):
        self.get_calls.append((url, copy.deepcopy(kwargs)))
        response = self.get_responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


@pytest.fixture(autouse=True)
def no_real_session(monkeypatch):
    monkeypatch.setattr(requests, 'Session',
                        lambda: pytest.fail('real requests.Session was constructed'))


def client(session):
    return JQuantsClient(REFRESH, session=session)


def test_auth_and_two_page_request_contract():
    session = Session(gets=[
        Response({'rows': [{'id': 1}], 'pagination_key': 'page-2'}),
        Response({'rows': [{'id': 2}]}),
    ])
    instance = client(session)
    assert session.post_calls == [(instance.base+'/token/auth_refresh', {
        'params': {'refreshtoken': REFRESH}, 'timeout': 30})]
    original = {'from': '2026-09-01', 'to': '2026-09-02'}
    assert instance.rows('/fixture', 'rows', **original) == [{'id': 1}, {'id': 2}]
    assert original == {'from': '2026-09-01', 'to': '2026-09-02'}
    assert session.get_calls == [
        (instance.base+'/fixture', {'headers': {'Authorization': 'Bearer id-token'},
                                    'params': original, 'timeout': 60}),
        (instance.base+'/fixture', {'headers': {'Authorization': 'Bearer id-token'},
                                    'params': {**original, 'pagination_key': 'page-2'},
                                    'timeout': 60}),
    ]


@pytest.mark.parametrize('terminal', [pytest.param({}, id='missing'),
                                      {'pagination_key': None},
                                      {'pagination_key': ''}])
def test_only_documented_empty_pagination_values_finish(terminal):
    session = Session(gets=[Response({'rows': [{'ok': True}], **terminal})])
    assert client(session).rows('/fixture', 'rows') == [{'ok': True}]
    assert len(session.get_calls) == 1


@pytest.mark.parametrize('where', ['auth', 'get'])
def test_http_status_failure_is_fixed_and_hides_sensitive_values(where):
    response = Response({'body': SECRET}, status=403)
    session = Session(post=response if where == 'auth' else None,
                      gets=[response] if where == 'get' else [])
    with pytest.raises(RuntimeError) as caught:
        instance = client(session)
        instance.rows('/fixture', 'rows')
    rendered = ''.join(traceback.format_exception(caught.value))
    assert SECRET not in rendered
    assert REFRESH not in rendered
    assert 'id-token' not in rendered
    assert '403' in str(caught.value)


@pytest.mark.parametrize('where', ['auth', 'get'])
@pytest.mark.parametrize('kind', ['request', 'json'])
def test_transport_and_json_errors_hide_original_exception(where, kind):
    error = (requests.RequestException(SECRET)
             if kind == 'request' else ValueError(SECRET))
    broken = error if kind == 'request' else Response(error=error)
    session = Session(post=broken if where == 'auth' else None,
                      gets=[broken] if where == 'get' else [])
    with pytest.raises(RuntimeError) as caught:
        instance = client(session)
        instance.rows('/fixture', 'rows')
    rendered = ''.join(traceback.format_exception(caught.value))
    assert SECRET not in str(caught.value)
    assert SECRET not in rendered
    assert caught.value.__suppress_context__ is True


@pytest.mark.parametrize('payload', [None, [], 'token', 1,
    {'idToken': None}, {'idToken': ''}, {'idToken': '   '},
    {'idToken': True}, {'idToken': 1}, {'idToken': []}])
def test_invalid_auth_payload_or_token_is_rejected(payload):
    with pytest.raises(RuntimeError):
        client(Session(post=Response(payload)))


@pytest.mark.parametrize('payload', [None, [], {'rows': None}, {'rows': {}},
                                      {'rows': [1]}, {'rows': [{} , 'bad']}])
def test_rows_payload_requires_list_of_objects(payload):
    with pytest.raises(ValueError):
        client(Session(gets=[Response(payload)])).rows('/fixture', 'rows')


@pytest.mark.parametrize('token', [True, 1, [], {}])
def test_non_string_pagination_key_is_rejected(token):
    payload = {'rows': [{}], 'pagination_key': token}
    with pytest.raises(ValueError):
        client(Session(gets=[Response(payload)])).rows('/fixture', 'rows')


def test_repeated_pagination_key_is_rejected():
    page = {'rows': [{}], 'pagination_key': 'same'}
    session = Session(gets=[Response(page), Response(page)])
    with pytest.raises(ValueError):
        client(session).rows('/fixture', 'rows')
    assert len(session.get_calls) == 2

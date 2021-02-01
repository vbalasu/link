from http import HTTPStatus
#from pytest_chalice.handlers import RequestHandler
import app
from chalice.test import Client

def test_index_with_url():
    with Client(app.app) as client:
        response = client.http.get('/?url=https://google.com')
        assert response.status_code == HTTPStatus.MOVED_PERMANENTLY
        assert response.headers['Location'] is not None

def test_index_without_url():
    with Client(app.app) as client:
        response = client.http.get('/')
        assert response.body == b'Invalid or missing url'


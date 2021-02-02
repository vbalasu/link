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

def test_link_received_by_sns():
    with Client(app.app) as client:
        with open('sns_message.txt') as f:
            event = client.events.generate_sns_event(message=f.read())
        with open('/tmp/event.json', 'w') as f:
            import json
            f.write(json.dumps(event))
        response = client.lambda_.invoke('handle_link_visit', event)
        assert response.payload['message'] == 'link visited'
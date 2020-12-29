from chalice import Chalice, Response
app = Chalice(app_name='link')

from cloudevents.http import CloudEvent, to_json
import os, boto3
#os.environ['AWS_PROFILE'] = 'vbalasu_admin'

@app.route('/')
def index():
    if app.current_request.query_params and app.current_request.query_params['url']:
        url = app.current_request.query_params['url']
    else:
        url = 'invalid'
    event = CloudEvent({'type': 'click', 'source': 'link.cloudmatica.com'}, {'url': url})
    import boto3
    boto3.client('sns').publish(TopicArn='arn:aws:sns:us-east-1:251566558623:cloud_events', 
                                Message=to_json(event).decode())
    return Response(status_code=301, body='', headers={'Location': url})

@app.route('/invalid')
def invalid():
    return 'Invalid or missing url'


# The view function above will return {"hello": "world"}
# whenever you make an HTTP GET request to '/'.
#
# Here are a few more examples:
#
# @app.route('/hello/{name}')
# def hello_name(name):
#    # '/hello/james' -> {"hello": "james"}
#    return {'hello': name}
#
# @app.route('/users', methods=['POST'])
# def create_user():
#     # This is the JSON body the user sent in their POST request.
#     user_as_json = app.current_request.json_body
#     # We'll echo the json body back to the user in a 'user' key.
#     return {'user': user_as_json}
#
# See the README documentation for more examples.
#

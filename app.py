from chalice import Chalice, Response
app = Chalice(app_name='link')

@app.route('/')
def index():
    if app.current_request.query_params and app.current_request.query_params['url']:
        url = app.current_request.query_params['url']
    else:
        return 'Invalid or missing url' #url = 'invalid'
    import boto3, json
    boto3.client('sns').publish(TopicArn='arn:aws:sns:us-east-1:251566558623:link', 
                                Message=json.dumps(app.current_request.to_dict()))
    return Response(status_code=301, body='', headers={'Location': url})

@app.on_sns_message(topic='link')
def handle_sns_message(event):
    app.log.debug("Received message with subject: %s, message: %s",
                  event.subject, event.message)
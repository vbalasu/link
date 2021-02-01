# link

Link redirection service running at https://link.cloudmatica.com

You can enter a link of the form [https://link.cloudmatica.com/?url=https://en.wikipedia.org](https://link.cloudmatica.com/?url=https://en.wikipedia.org)

This will redirect to the url specified in the url parameter. Before doing that, it publishes a message to the `link` SNS topic which in turn flows to the `link` SQS queue. 


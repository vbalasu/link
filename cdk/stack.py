"""CDK stack for one link shortener domain.

CloudFront (ACM cert, TLS) -> Lambda Function URL (redirect handler) -> DynamoDB.
Redirects are never edge-cached, so every click reaches the Lambda and is recorded.
Route53 alias records point {subdomain}.{domain} at the distribution.
"""

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_certificatemanager as acm,
    aws_cloudfront as cf,
    aws_cloudfront_origins as origins,
    aws_dynamodb as ddb,
    aws_lambda as lambda_,
    aws_route53 as r53,
    aws_route53_targets as r53t,
)
from constructs import Construct


class LinkStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        domain: str,
        subdomain: str,
        root_redirect: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        host = f"{subdomain}.{domain}" if subdomain else domain

        # Single-table design: link items (SK=META) + click items (SK=ts#uuid).
        # RETAIN so a `cdk destroy` never silently drops click history.
        table = ddb.Table(
            self,
            "Links",
            partition_key=ddb.Attribute(name="PK", type=ddb.AttributeType.STRING),
            sort_key=ddb.Attribute(name="SK", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
            point_in_time_recovery=True,
            removal_policy=RemovalPolicy.RETAIN,
        )

        fn = lambda_.Function(
            self,
            "Redirect",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset("lambda"),
            timeout=Duration.seconds(10),
            memory_size=128,
            environment={
                "TABLE_NAME": table.table_name,
                "ROOT_REDIRECT": root_redirect,
            },
        )
        table.grant_read_write_data(fn)

        furl = fn.add_function_url(auth_type=lambda_.FunctionUrlAuthType.NONE)

        zone = r53.HostedZone.from_lookup(self, "Zone", domain_name=domain)

        cert = acm.Certificate(
            self,
            "Cert",
            domain_name=host,
            validation=acm.CertificateValidation.from_dns(zone),
        )

        # Forward exactly the headers the handler reads. Note: Host is intentionally
        # NOT forwarded, so CloudFront sends the Function URL's own host to the origin.
        origin_request_policy = cf.OriginRequestPolicy(
            self,
            "OriginRequestPolicy",
            header_behavior=cf.OriginRequestHeaderBehavior.allow_list(
                "CloudFront-Viewer-Address",
                "CloudFront-Viewer-Country",
                "CloudFront-Viewer-Country-Region",
                "CloudFront-Viewer-City",
                "User-Agent",
                "Referer",
                "Accept-Language",
            ),
            query_string_behavior=cf.OriginRequestQueryStringBehavior.none(),
            cookie_behavior=cf.OriginRequestCookieBehavior.none(),
        )

        distribution = cf.Distribution(
            self,
            "Distribution",
            default_behavior=cf.BehaviorOptions(
                origin=origins.FunctionUrlOrigin(furl),
                viewer_protocol_policy=cf.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                cache_policy=cf.CachePolicy.CACHING_DISABLED,
                origin_request_policy=origin_request_policy,
                allowed_methods=cf.AllowedMethods.ALLOW_ALL,
            ),
            domain_names=[host],
            certificate=cert,
            price_class=cf.PriceClass.PRICE_CLASS_100,
            comment=f"link shortener for {host}",
        )

        alias_target = r53.RecordTarget.from_alias(r53t.CloudFrontTarget(distribution))
        r53.ARecord(self, "AliasA", zone=zone, record_name=host, target=alias_target)
        r53.AaaaRecord(self, "AliasAAAA", zone=zone, record_name=host, target=alias_target)

        CfnOutput(self, "TableName", value=table.table_name)
        CfnOutput(self, "LinkHost", value=host)
        CfnOutput(self, "DistributionDomain", value=distribution.distribution_domain_name)
        CfnOutput(self, "FunctionUrl", value=furl.url)

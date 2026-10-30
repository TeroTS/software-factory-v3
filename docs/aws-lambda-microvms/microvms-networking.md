

# Networking
<a name="microvms-networking"></a>

You configure network access for your AWS Lambda MicroVMs by associating Network Connector resources with your MicroVM at run time. Network connectors are specified when you call `run-microvm` and cannot be changed while a MicroVM is running.

## Overview
<a name="microvms-networking-overview"></a>

Each MicroVM can have independent ingress (inbound) and egress (outbound) network configurations:
+ **Ingress network connectors** enable inbound connectivity. Clients connect to a service-managed HTTPS endpoint, and Lambda forwards traffic to ports you configure within the MicroVM. Ingress connectors are AWS-managed – you reference them by ARN when running a MicroVM.
+ **Egress network connectors** enable outbound traffic. By default, MicroVMs have public internet access. You can create a customer-managed VPC egress connector to route outbound traffic through your VPC instead.

A single connector can be reused across many MicroVMs – this is the intended usage pattern.

## Inbound connectivity
<a name="microvms-networking-inbound"></a>

Each Lambda MicroVM is reachable at a unique HTTPS endpoint URL, assigned when you call `run-microvm`. Clients send requests to this endpoint over HTTPS. Lambda routes each request to a port inside your MicroVM, where your application receives it.

By default, requests received at the endpoint are routed to port 8080 inside the MicroVM. To route to a different port, see [Port routing](#microvms-networking-port-routing).

The following protocols are supported on the inbound endpoint:
+ HTTP/1.1
+ HTTP/2
+ WebSockets
+ gRPC
+ Server-Sent Events (SSE)

**Note**  
Traffic between your client and the MicroVM endpoint is always encrypted with TLS. Your application can serve requests over either HTTP or HTTPS internally.

### Port routing
<a name="microvms-networking-port-routing"></a>

Lambda selects the target port inside your MicroVM using the following order of priority:

1. **`X-aws-proxy-port` header** – For standard HTTP requests, include this header with the target port number.

1. **WebSocket subprotocol** – If your WebSocket client can't set custom headers, specify the target port as a subprotocol named `lambda-microvms.port.{{N}}`, where {{N}} is the port number. You provide subprotocols when you open the WebSocket connection. For an example, see [Protocols](microvms-launching.md#microvms-launching-websocket).

1. **Default (8080)** – If neither is specified, requests route to port 8080.

**Important**  
The target port must be within the `allowedPorts` defined in the authentication token. Requests to unauthorized ports receive a 403 Forbidden response.

### Authentication
<a name="microvms-networking-auth"></a>

All requests to a MicroVM endpoint require a valid authentication token in the `X-aws-proxy-auth` header. You generate tokens using `create-microvm-auth-token`. Each token is an encrypted JWE (JSON Web Encryption) string scoped to:
+ A specific MicroVM (identified by ID).
+ A set of allowed ports (single port, range, or all ports).
+ An expiration time (configured at token creation).

The following example creates a token and uses it to send an authenticated request:

```
aws lambda-microvms create-microvm-auth-token \
  --microvm-identifier {{microvm-id}} \
  --expiration-in-minutes 30 \
  --allowed-ports '[{"port":8080}]'
```

```
curl 'https://{{microvm-endpoint}}' \
  -H 'X-aws-proxy-auth: {{TOKEN}}' \
  -H 'X-aws-proxy-port: 8080'
```

For a complete walkthrough of creating tokens and connecting to a MicroVM, including WebSocket connections, see [Connecting to a MicroVM](microvms-launching.md#microvms-launching-connecting).

### Error responses
<a name="microvms-networking-errors"></a>

The following HTTP status codes are returned by the MicroVM endpoint when it cannot process or deliver a request to your application. These responses come from the endpoint, not from your application.


| Code | Status | Cause and resolution | 
| --- | --- | --- | 
| 400 | Bad Request | Malformed request, or an invalid port header or WebSocket subprotocol. Verify the format. | 
| 403 | Forbidden | Missing, expired, or invalid token; or the requested port is not in the token's allowedPorts. Generate a new token, or use an allowed port. | 
| 429 | Too Many Requests | Rate limit exceeded (account-level or per-MicroVM). Retry with exponential backoff. | 
| 500 | Internal Server Error | An internal error occurred. Retry the request. | 
| 502 | Bad Gateway | Application not responding, or auto-resume did not succeed within the maximum number of retry attempts. See [Auto-resume](microvms-launching.md#microvms-launching-auto-resume). | 

### Request headers
<a name="microvms-networking-headers"></a>

The `X-aws-proxy-*` header namespace is reserved by Lambda for request metadata, such as the authentication token (`X-aws-proxy-auth`) and target port (`X-aws-proxy-port`). Lambda removes `X-aws-proxy-*` headers before forwarding the request to your application.

### Request/response bandwidth
<a name="microvms-networking-bandwidth"></a>

Each Lambda MicroVM has a request/response bandwidth that scales linearly with its size. This bandwidth applies to all traffic through the MicroVM endpoint, both inbound requests and outbound responses.


| MicroVM size (baseline) | Max bandwidth | 
| --- | --- | 
| 0.5 GB, 0.25 vCPU | 1 MB/s (8 Mbps) | 
| 1 GB, 0.5 vCPU | 2 MB/s (16 Mbps) | 
| 2 GB, 1 vCPU | 4 MB/s (32 Mbps) | 
| 4 GB, 2 vCPU | 8 MB/s (64 Mbps) | 
| 8 GB, 4 vCPU | 16 MB/s (128 Mbps) | 

If you experience increased request latency due to network saturation, either reduce your request concurrency or payload size, or select a larger MicroVM size to increase the available bandwidth.

### HTTP/2 support
<a name="microvms-networking-http2"></a>

Lambda MicroVMs supports HTTP/2 on the inbound endpoint. Lambda negotiates the protocol through ALPN (Application-Layer Protocol Negotiation) during the TLS handshake, preferring HTTP/2 and falling back to HTTP/1.1. An HTTP/2-capable client uses it automatically.

To use HTTP/2 between the endpoint and your application inside the MicroVM:
+ **Your application serves TLS** – Lambda negotiates HTTP/2 with your application through ALPN, falling back to HTTP/1.1 if HTTP/2 isn't supported.
+ **Your application serves plaintext HTTP** – Include the `X-aws-proxy-force-h2: true` header in your request to use HTTP/2 on the connection to your application.

## Outbound connectivity
<a name="microvms-networking-outbound"></a>

By default, Lambda MicroVMs have public internet access on the egress path. To connect MicroVMs with resources in your private VPCs – such as RDS, ElastiCache, internal APIs, and on-premises systems through Direct Connect or VPN – create a Lambda Network Connector with your VPC configuration.

When using VPC egress, outbound traffic is subject to security group rules and network ACLs governing traffic in your VPC.

## Working with egress network connectors
<a name="microvms-networking-connectors"></a>

Egress network connectors route outbound traffic from your MicroVM through your VPC. You create a connector once, then reference it by ARN when starting MicroVMs through the `run-microvm` command.

### Prerequisites
<a name="microvms-networking-connectors-prereqs"></a>

Before creating a network connector, you need an IAM role that allows Lambda to create elastic network interfaces (ENIs) in your VPC. The role requires the following permissions:

```
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "CreateENI",
      "Effect": "Allow",
      "Action": "ec2:CreateNetworkInterface",
      "Resource": [
        "arn:aws:ec2:*:*:network-interface/*",
        "arn:aws:ec2:*:*:subnet/*",
        "arn:aws:ec2:*:*:security-group/*"
      ]
    },
    {
      "Sid": "TagENI",
      "Effect": "Allow",
      "Action": "ec2:CreateTags",
      "Resource": "arn:aws:ec2:*:*:network-interface/*",
      "Condition": {
        "StringEquals": {
          "ec2:ManagedResourceOperator": "network-connectors.lambda.amazonaws.com"
        }
      }
    }
  ]
}
```

### Creating a network connector
<a name="microvms-networking-connectors-create"></a>

Create a connector by specifying your VPC subnets, security groups, and network protocol (`IPv4` or `DualStack`):

```
aws lambda-core create-network-connector \
  --name my-connector \
  --configuration '{
    "VpcEgressConfiguration": {
      "SubnetIds": ["{{subnet-xxx}}"],
      "SecurityGroupIds": ["{{sg-xxx}}"],
      "NetworkProtocol": "IPv4",
      "AssociatedComputeResourceTypes": ["MicroVm"]
    }
  }' \
  --operator-role arn:aws:iam::{{123456789012}}:role/NetworkConnectorOperatorRole
```

### Network connector states
<a name="microvms-networking-connectors-states"></a>

A connector must be in `ACTIVE` state before you can reference it in `run-microvm`.


| State | Description | 
| --- | --- | 
| PENDING | Connector is being created (underlying ENIs are being provisioned). | 
| ACTIVE | Connector is ready to use. | 
| INACTIVE | Connector is temporarily inactive. | 
| FAILED | Provisioning or update failed. Check StateReason. | 
| DELETING | Connector is being deleted; ENIs are being cleaned up. | 
| DELETE\_FAILED | Deletion failed. | 

### Network connector versioning
<a name="microvms-networking-connectors-versioning"></a>

Versioning applies to VPC egress network connectors (connector type `VPC_EGRESS`). A VPC egress connector is versioned: each successful `update-network-connector` call creates a new version, and earlier versions remain available.

When you run a MicroVM, you reference a VPC egress connector by ARN in `--egress-network-connectors`. You can use either form:
+ **Unqualified ARN** – resolves to the latest version of the connector. A MicroVM picks up configuration changes automatically. `arn:aws:lambda:{{us-east-1}}:{{123456789012}}:network-connector:{{nc-id}}`
+ **Qualified ARN** – pins the MicroVM to a specific version. Use this to hold a MicroVM on a known configuration for testing, troubleshooting, or rollback. `arn:aws:lambda:{{us-east-1}}:{{123456789012}}:network-connector:{{nc-id}}:{{version-number}}`

A MicroVM moves to a new version only when you change the ARN it references, so you can adopt new versions on your own schedule.

Example `run-microvm` call pinning a specific connector version:

```
aws lambda-microvms run-microvm \
  --image-identifier {{arn:aws:lambda:us-east-1:123456789012:microvm-image:my-microvm-image}} \
  --egress-network-connectors {{arn:aws:lambda:us-east-1:123456789012:network-connector:nc-id:version-number}} \
  --idle-policy '{"maxIdleDurationSeconds":900,"suspendedDurationSeconds":1800,"autoResumeEnabled":false}'
```

### Running a MicroVM with a network connector
<a name="microvms-networking-connectors-run"></a>

Reference the connector ARN when running a MicroVM:

```
aws lambda-microvms run-microvm \
  --image-identifier arn:aws:lambda:us-east-1:{{123456789012}}:microvm-image:my-microvm-image \
  --egress-network-connectors {{connector-arn}} \
  --idle-policy '{"maxIdleDurationSeconds":900,"suspendedDurationSeconds":1800,"autoResumeEnabled":false}'
```

**Note**  
Before you update or delete a connector, ensure all MicroVMs using it have terminated. Modifying a connector that is actively in use can cause network connectivity issues for running MicroVMs.

## Using Lambda MicroVMs with interface VPC endpoints (AWS PrivateLink)
<a name="microvms-networking-privatelink"></a>

You can use AWS PrivateLink for private connectivity over the AWS network between your VPC resources and Lambda MicroVMs, without traversing the public internet. MicroVMs supports two VPC endpoints, depending on the desired traffic destination:
+ **MicroVM management APIs** (create images, run, suspend, terminate) – uses the existing Lambda VPC endpoint (`com.amazonaws.{{region}}.lambda`).
+ **Connectivity to MicroVMs** (HTTPS traffic to your running applications) – uses a separate endpoint (`com.amazonaws.{{region}}.lambda-microvm`).

### VPC endpoint for MicroVM management APIs
<a name="microvms-networking-privatelink-mgmt"></a>

Lambda MicroVMs shares the same VPC endpoint service as Lambda (`com.amazonaws.{{region}}.lambda`). For full instructions, see [Creating an interface endpoint for Lambda](https://docs.aws.amazon.com/lambda/latest/dg/configuration-vpc-endpoints.html#vpc-endpoint-create).

#### Endpoint policy for MicroVM management APIs
<a name="microvms-networking-privatelink-mgmt-policy"></a>

To control who can use your interface endpoint and which Lambda MicroVMs API actions they can perform, attach an endpoint policy. The policy specifies the principal that can perform actions, the actions they can perform, and the resources they can act on. Lambda MicroVMs actions use the `lambda:` IAM action prefix.

For more information, see [Controlling access to services with VPC endpoints](https://docs.aws.amazon.com/vpc/latest/privatelink/vpc-endpoints-access.html) in the *Amazon VPC User Guide*.

The following example policy allows user `MyUser` to list and get MicroVM images through the endpoint:

```
{
  "Statement": [
    {
      "Principal": {
        "AWS": "arn:aws:iam::111122223333:user/MyUser"
      },
      "Effect": "Allow",
      "Action": [
        "lambda:ListMicrovmImages",
        "lambda:GetMicrovmImage"
      ],
      "Resource": "*"
    }
  ]
}
```

### VPC endpoint for MicroVM connectivity
<a name="microvms-networking-privatelink-data"></a>

To keep HTTPS traffic to your running MicroVMs private, create an interface endpoint for the `com.amazonaws.{{region}}.lambda-microvm` service. This endpoint handles connections to MicroVM endpoint URLs (for example, `abc123def456.lambda-microvm.us-east-1.on.aws`).

To learn more about interface endpoint properties, review the [interface endpoints guide](https://docs.aws.amazon.com/vpc/latest/privatelink/interface-endpoints.html) in the *Amazon VPC documentation*.

#### Creating the endpoint
<a name="microvms-networking-privatelink-data-create"></a>

**To create an interface endpoint for MicroVM connectivity (console)**

1. Open the [Endpoints page](https://console.aws.amazon.com/vpc/home#Endpoints) of the Amazon VPC console.

1. Choose **Create endpoint**.

1. For **Service category**, verify that **AWS services** is selected.

1. For **Service Name**, choose `com.amazonaws.{{region}}.lambda-microvm`. Verify that the Type is **Interface**.

1. Choose a VPC and subnets.

1. To enable private DNS for the interface endpoint, select the **Enable DNS name** check box (recommended). This ensures that requests using the public MicroVM endpoint hostname automatically resolve to your interface endpoint, with no client-side changes required.

1. For **Security group**, choose one or more security groups. The security group must allow outbound TCP traffic on port 443 to the endpoint network interfaces.

1. Choose **Create endpoint**.

To use the private DNS option, you must set the `enableDnsHostnames` and `enableDnsSupport` attributes of your VPC. For more information, see [Viewing and updating DNS support for your VPC](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-dns.html#vpc-dns-updating) in the *Amazon VPC User Guide*.

**To create an interface endpoint for MicroVM connectivity (AWS CLI)**

```
aws ec2 create-vpc-endpoint \
  --vpc-id {{vpc-ec43eb89}} \
  --vpc-endpoint-type Interface \
  --service-name com.amazonaws.{{us-east-1}}.lambda-microvm \
  --subnet-id {{subnet-abababab}} \
  --security-group-id {{sg-1a2b3c4d}} \
  --private-dns-enabled
```

To verify that the endpoint is available and that private DNS is in effect:

```
aws ec2 describe-vpc-endpoints \
  --vpc-endpoint-ids {{vpce-1a2b3c4d5e6f7g8h9}} \
  --query 'VpcEndpoints[0].{State:State,PrivateDns:PrivateDnsEnabled,Dns:DnsEntries[*].DnsName}'
```

#### Private DNS behavior
<a name="microvms-networking-privatelink-data-dns"></a>

**When private DNS is enabled** – The endpoint manages DNS resolution for `*.lambda-microvm.{{region}}.on.aws` inside your VPC. Your existing MicroVM endpoint hostnames (for example, `abc123def456.lambda-microvm.us-east-1.on.aws`) resolve to the private IP addresses of the endpoint network interfaces. No client change is required.

**When private DNS is disabled** – Amazon VPC generates an endpoint-specific DNS name for your endpoint in the form `{{vpce-id-hash}}.lambda-microvm.{{region}}.vpce.amazonaws.com`. To route traffic through this endpoint while still reaching the correct MicroVM, you must preserve the original MicroVM hostname in two places:
+ **TLS Server Name Indication (SNI)** – The TLS handshake uses this value to identify which MicroVM the connection is for.
+ **HTTP Host header** – The proxy uses this value to route the request to the correct MicroVM.

If either value is set to the VPC endpoint hostname instead of the MicroVM hostname, the connection cannot be routed to the correct MicroVM.

**Example: Connect through an endpoint-specific DNS name**

The following example uses [curl](https://curl.se/) with the `--connect-to` flag to redirect the TCP connection to your VPC endpoint while keeping the MicroVM hostname in the URL, SNI, and Host header:

```
ENDPOINT_HOST=abc123def456.lambda-microvm.us-east-1.on.aws
VPCE_HOST=vpce-0a1b2c3d4e5f67890-a1b2c3d4.lambda-microvm.us-east-1.vpce.amazonaws.com

curl --connect-to "$ENDPOINT_HOST:443:$VPCE_HOST:443" \
  -H "x-aws-proxy-auth: $MICROVM_AUTH_TOKEN" \
  -H "x-aws-proxy-port: 8080" \
  "https://$ENDPOINT_HOST/"
```

The `--connect-to` flag tells curl to open the TCP connection to the VPC endpoint address, while the URL, TLS SNI, and Host header remain set to your MicroVM hostname.

For more information, see [Accessing a service through an interface endpoint](https://docs.aws.amazon.com/vpc/latest/privatelink/interface-endpoints.html#access-service-though-endpoint) in the *Amazon VPC User Guide*.

#### Endpoint policy for MicroVM connectivity
<a name="microvms-networking-privatelink-data-policy"></a>

You can attach an endpoint policy to control which MicroVMs are reachable through the `lambda-microvm` VPC endpoint. An endpoint policy on the `lambda-microvm` service lets you scope connections to specific accounts or organizations. By default, the endpoint allows connections to MicroVMs in any AWS account. (Note: The client establishing the connection must still hold a valid MicroVM auth token to be granted access).

By default, a VPC endpoint has a full-access policy that allows all traffic. When you replace the default policy with a custom policy, Lambda MicroVMs evaluates that policy against the `lambda:ConnectMicrovm` action on every connection made through the endpoint. If the policy does not allow connecting to MicroVMs, the connection is rejected with an HTTP 403 Forbidden response. A policy that does not grant `lambda:ConnectMicrovm` denies all connections through the endpoint.

**Note**  
The `lambda:ConnectMicrovm` action authorizes a connection to a MicroVM endpoint through the interface endpoint. It is not a Lambda API operation and cannot be used in IAM identity-based or resource-based policies – it is valid only in a VPC endpoint policy.

**Principal and resource**

Connections to a MicroVM endpoint are authenticated with a MicroVM auth token rather than AWS Signature Version 4. For this reason, no IAM principal is associated with the connection. Instead, Lambda MicroVMs evaluates the endpoint policy with an anonymous principal. This means:
+ `Principal` must be `"*"`. A policy that names a specific principal matches nothing and denies every connection.
+ Condition keys that depend on requester identity (such as `aws:PrincipalArn`, `aws:PrincipalOrgID`, and `aws:userid`) are not populated and will not match.
+ `Resource` must also be `"*"`. Lambda MicroVMs does not scope endpoint policy evaluation to individual MicroVM resource ARNs. To restrict which MicroVMs the endpoint can reach, use the `aws:ResourceAccount` condition key rather than the `Resource` element.

**Supported condition keys**


| Condition key | Description | 
| --- | --- | 
| aws:ResourceAccount | The AWS account that owns the MicroVM being connected to. | 
| aws:ResourceOrgID | The AWS Organizations organization ID of the account that owns the MicroVM. | 
| aws:SourceVpce | The ID of the interface endpoint the connection passed through. | 
| aws:SourceVpc | The ID of the VPC the connection originated from. | 
| aws:VpcSourceIp | The private IP address of the client that made the connection. | 

**Example: Allow connections only to MicroVMs in your own account**

The following endpoint policy allows connections through the endpoint only to MicroVMs owned by account 111122223333. Connections to MicroVMs owned by any other account are denied.

```
{
  "Statement": [
    {
      "Principal": "*",
      "Effect": "Allow",
      "Action": "lambda:ConnectMicrovm",
      "Resource": "*",
      "Condition": {
        "StringEquals": {
          "aws:ResourceAccount": "111122223333"
        }
      }
    }
  ]
}
```
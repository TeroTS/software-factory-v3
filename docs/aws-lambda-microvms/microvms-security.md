

# Security and permissions
<a name="microvms-security"></a>

This section describes the IAM roles, authentication tokens, and access controls for AWS Lambda MicroVMs.

## IAM roles
<a name="microvms-security-iam-roles"></a>

### Build role
<a name="microvms-security-build-role"></a>

This role is used during image creation. It requires the following permissions to retrieve source artifacts and generate logs: `s3:GetObject`, `logs:CreateLogGroup`, `logs:CreateLogStream`, and `logs:PutLogEvents`. Also add `ecr:GetAuthorizationToken` if referencing private ECR images.

The build role is optional. If you do not provide a build role, Lambda cannot write build logs to CloudWatch.

### Execution role
<a name="microvms-security-execution-role"></a>

This role is used at MicroVM runtime to provide access permissions to write logs to CloudWatch and use other AWS services.

The execution role is optional. If you do not provide an execution role, Lambda does not emit runtime logs to CloudWatch and your MicroVM cannot access other AWS services.

Lifecycle hooks execute under the role associated with their phase. Build-time hooks (`/ready` and `/validate`) execute under the build role. Runtime hooks (`/run`, `/resume`, `/suspend`, and `/terminate`) execute under the execution role.

### Trust policies
<a name="microvms-security-trust-policies"></a>

Both roles require a trust policy that allows the Lambda service principal `lambda.amazonaws.com` to perform `sts:AssumeRole` and `sts:TagSession`.

## Authentication tokens
<a name="microvms-security-auth-tokens"></a>

Tokens control inbound access to running MicroVMs. They are port-scoped; each token specifies which ports it grants access to.

```
# Token granting access to all ports
aws lambda-microvms create-microvm-auth-token \
  --microvm-identifier <id> \
  --expiration-in-minutes 30 \
  --allowed-ports '[{"allPorts":{}}]'

# Token granting access to port 8080 only
aws lambda-microvms create-microvm-auth-token \
  --microvm-identifier <id> \
  --expiration-in-minutes 30 \
  --allowed-ports '[{"port":8080}]'
```

## Shell access tokens
<a name="microvms-security-shell-tokens"></a>

Shell access uses a separate token API. The target MicroVM must have been run with the `SHELL_INGRESS` network connector (`arn:aws:lambda:{{us-east-1}}:aws:network-connector:aws-network-connector:SHELL_INGRESS`). If the MicroVM was not launched with this connector, `create-microvm-shell-auth-token` fails with a `ValidationException`:

```
aws lambda-microvms create-microvm-shell-auth-token \
  --microvm-identifier <id> \
  --expiration-in-minutes 30
```

For a full walkthrough of connecting to a MicroVM shell, see [Shell access](microvms-troubleshooting.md#microvms-troubleshooting-shell).

## Tagging resources
<a name="microvms-security-tagging"></a>

Lambda MicroVM images support AWS resource tags for organization and cost allocation. Add tags when creating an image using the `--tags` parameter or explicitly after creation. You can also list and delete tags, as demonstrated below: You can also reference tags in IAM policy conditions to control access to MicroVM resources.

```
# Add tags when creating an image
aws lambda-microvms create-microvm-image \
  --name my-app-image \
  --code-artifact '{"uri":"s3://my-bucket/app.zip"}' \
  --base-image-arn arn:aws:lambda:{{us-east-1}}:aws:microvm-image:al2023-1 \
  --build-role-arn arn:aws:iam::123456789012:role/MicrovmBuildRole \
  --tags '{"Environment":"production","Team":"platform"}'

# List tags on a resource
aws lambda-microvms list-tags \
  --resource arn:aws:lambda:us-east-1:123456789012:microvm-image:my-app-image

# Add tags to an existing resource
aws lambda-microvms tag-resource \
  --resource arn:aws:lambda:us-east-1:123456789012:microvm-image:my-app-image \
  --tags '{"CostCenter":"12345","Project":"sandbox-platform"}'

# Remove tags by key
aws lambda-microvms untag-resource \
  --resource arn:aws:lambda:us-east-1:123456789012:microvm-image:my-app-image \
  --tag-keys '["CostCenter","Project"]'
```

## IAM permissions reference
<a name="microvms-security-iam-reference"></a>

Lambda MicroVMs defines the following IAM actions. Use these in IAM policies to control access to MicroVM operations.


| Action | Description | 
| --- | --- | 
| lambda:CreateMicrovmImage | Create a new MicroVM image. | 
| lambda:UpdateMicrovmImage | Update an existing MicroVM image. | 
| lambda:DeleteMicrovmImage | Delete a MicroVM image. | 
| lambda:GetMicrovmImage | Get MicroVM image details. | 
| lambda:ListMicrovmImages | List MicroVM images in the account. | 
| lambda:RunMicrovm | Run a new MicroVM. | 
| lambda:GetMicrovm | Get MicroVM state and details. | 
| lambda:ListMicrovms | List MicroVMs in the account. | 
| lambda:SuspendMicrovm | Suspend a running MicroVM. | 
| lambda:ResumeMicrovm | Resume a suspended MicroVM. | 
| lambda:TerminateMicrovm | Terminate a MicroVM. | 
| lambda:CreateMicrovmAuthToken | Generate an authentication token for a MicroVM. | 
| lambda:CreateMicrovmShellAuthToken | Generate a shell access token. | 

## Resource ARN formats
<a name="microvms-security-resource-arns"></a>

```
# MicroVM image
arn:aws:lambda:<region>:<account>:microvm-image:<image-name>
# Network connector
arn:aws:lambda:<region>:<account>:network-connector:<connector-id>
```

## Example: Least-privilege policy for an operator
<a name="microvms-security-least-privilege"></a>

```
{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect": "Allow",
        "Action": [
          "lambda:ListMicrovms",
          "lambda:ListMicrovmImages"
        ],
        "Resource": "*"
      },
      {
        "Effect": "Allow",
        "Action": [
          "lambda:RunMicrovm",
          "lambda:GetMicrovm",
          "lambda:SuspendMicrovm",
          "lambda:ResumeMicrovm",
          "lambda:TerminateMicrovm",
          "lambda:CreateMicrovmAuthToken",
          "lambda:GetMicrovmImage"
        ],
        "Resource": "arn:aws:lambda:*:123456789012:microvm-image:*"
      }
    ]
}
```
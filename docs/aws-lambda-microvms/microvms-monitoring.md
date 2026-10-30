

# Monitoring
<a name="microvms-monitoring"></a>

This page covers CloudWatch logging (build and runtime) and CloudTrail auditing for AWS Lambda MicroVMs.

## Logging (CloudWatch Logs)
<a name="microvms-monitoring-logging"></a>

AWS Lambda MicroVMs streams logs to CloudWatch in two phases:

Build logs  
Lambda generates build logs during image creation, including output from `Dockerfile` execution, application startup, and hook invocations. These logs are streamed using the build role and written to the default log group `/aws/lambda-microvms/<image-name>`.

Runtime logs  
Lambda streams application stdout and stderr from running MicroVMs to CloudWatch. Runtime logs are streamed using the execution role and written to the same default log group, with the log stream defaulting to the MicroVM ID.

**Important**  
Without an execution role, application stdout and stderr are not forwarded to CloudWatch at runtime. To receive runtime logs, provide an execution role with `logs:CreateLogGroup`, `logs:CreateLogStream`, and `logs:PutLogEvents` permissions.

### Custom log destinations
<a name="microvms-monitoring-logging-custom"></a>

You can configure a custom log group and stream by using the `--logging` parameter on `run-microvm` or `create-microvm-image`:

```
--logging '{"cloudWatch":{"logGroup":"/my-app/microvms","logStream":"custom-stream"}}'
```

If you omit `logStream`, it defaults to the MicroVM ID.

You can explicitly disable logging by passing the following configuration:

```
--logging '{"disabled":{}}'
```

## CloudTrail logging
<a name="microvms-monitoring-cloudtrail"></a>

Lambda MicroVM is integrated with CloudTrail, a service that provides a record of actions taken by a user, role, or an AWS service. CloudTrail captures all API calls for Lambda MicroVMs as events. The calls captured include calls from the Lambda MicroVMs console and code calls to the Lambda MicroVMs API operations.

If you create a trail, you can enable continuous delivery of CloudTrail events to an Amazon S3 bucket, including events for Lambda MicroVMs. If you don't configure a trail, you can still view the most recent events in the CloudTrail console in Event history.

Using the information collected by CloudTrail, you can determine the request that was made to Lambda MicroVMs, the IP address from which the request was made, who made the request, when it was made, and additional details.

CloudTrail records two types of events for Lambda MicroVMs: management events (control plane operations like creating and deleting images) and data events (data plane operations like running and terminating MicroVMs). Management events are logged by default. Data events require explicit opt-in.

### Management events
<a name="microvms-monitoring-cloudtrail-mgmt"></a>

Lambda MicroVMs management events are control plane operations that are logged by default in CloudTrail. Management events include creating, updating, and deleting MicroVM images, as well as listing and describing resources.

The following Lambda MicroVMs actions are logged as management events:
+ `CreateMicrovmImage`
+ `DeleteMicrovmImage`
+ `DeleteMicrovmImageVersion`
+ `UpdateMicrovmImage`
+ `UpdateMicrovmImageVersion`
+ `ListMicrovmImages`
+ `GetMicrovmImage`
+ `ListMicrovmImageVersions`
+ `GetMicrovmImageVersion`
+ `ListMicrovmImageBuilds`
+ `GetMicrovmImageBuild`
+ `ListMicrovms`
+ `GetMicrovm`
+ `ListManagedMicrovmImages`

### Data events
<a name="microvms-monitoring-cloudtrail-data"></a>

Data events provide information about the resource operations performed on or within a resource (for example, running or terminating a MicroVM). These are also known as data plane operations. Data events are often high-volume activities. By default, CloudTrail doesn't log data events. You must explicitly enable data event logging for Lambda MicroVMs.

The CloudTrail resource type for Lambda MicroVMs data events is `AWS::Lambda::MicrovmImage`. You can use the CloudTrail console or the AWS CLI to configure a trail or event data store to log data events.

The following Lambda MicroVMs actions are logged as data events:
+ `RunMicrovm`
+ `TerminateMicrovm`
+ `SuspendMicrovm`
+ `ResumeMicrovm`
+ `CreateMicrovmAuthToken`
+ `CreateMicrovmShellAuthToken`

To log data events for all MicroVM images, use the following advanced event selector:

```
{
   "Name": "Log Lambda MicroVM data events",
   "FieldSelectors": [
     { "Field": "eventCategory", "Equals": ["Data"] },
     { "Field": "resources.type", "Equals": ["AWS::Lambda::MicrovmImage"] }
   ]
}
```

### Enabling data event logging
<a name="microvms-monitoring-cloudtrail-enable"></a>

To enable data event logging using the AWS CLI, run the `put-event-selectors` command with advanced event selectors:

```
aws cloudtrail put-event-selectors \
   --trail-name my-trail \
   --advanced-event-selectors '[{
     "Name": "Log Lambda MicroVM data events",
     "FieldSelectors": [
       { "Field": "eventCategory", "Equals": ["Data"] },
       { "Field": "resources.type", "Equals": ["AWS::Lambda::MicrovmImage"] }
     ]
   }]'
```

### Understanding CloudTrail log entries
<a name="microvms-monitoring-cloudtrail-entries"></a>

A CloudTrail trail delivers API activity records to an Amazon S3 bucket you specify. Each log entry represents a single API request and includes the caller identity, timestamp, request parameters, and response.

The following example shows a CloudTrail log entry that demonstrates a data event for the `RunMicrovm` action:

```
{
  "eventVersion": "1.11",
  "userIdentity": {
    "type": "AssumedRole",
    "principalId": "A1B2C3D4E5F6G7EXAMPLE:mySessionName",
    "arn": "arn:aws:sts::111122223333:assumed-role/MyRole/mySessionName",
    "accountId": "111122223333",
    "accessKeyId": "AKIAIOSFODNN7EXAMPLE"
  },
  "eventTime": "2026-04-20T18:26:44Z",
  "eventSource": "lambda.amazonaws.com",
  "eventName": "RunMicrovm",
  "awsRegion": "us-east-1",
  "sourceIPAddress": "127.0.0.1",
  "requestParameters": {
    "microvmImageArn": "arn:aws:lambda:us-east-1:111122223333:microvm-image:my-image"
  },
  "responseElements": {
    "microvmId": "ai-12345678-abcd-1234-ef56-123456789abc",
    "microvmState": "PENDING",
    "endpoint": "12345678-abcd-1234-ef56-123456789abc.lambda-microvm.us-east-1.on.aws",
    "microvmImageArn": "arn:aws:lambda:us-east-1:111122223333:microvm-image:my-image",
    "microvmImageVersion": "1.0"
  },
  "requestID": "7aebcd0f-cda1-11e4-aaa2-e356da31e4ff",
  "eventID": "e92a3e85-8ecd-4d23-8074-843aabfe89bf",
  "readOnly": false,
  "resources": [
    {
      "accountId": "111122223333",
      "type": "AWS::Lambda::MicrovmImage",
      "ARN": "arn:aws:lambda:us-east-1:111122223333:microvm-image:my-image"
    }
  ],
  "eventType": "AwsApiCall",
  "managementEvent": false,
  "recipientAccountId": "111122223333",
  "eventCategory": "Data"
}
```
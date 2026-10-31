# Build the Standard ASL lifecycle workflow; input is Job, output excludes auth tokens.
SDK = "arn:aws:states:::aws-sdk:lambdamicrovms:"
CONNECTOR = "arn:aws:lambda:us-east-1:aws:network-connector:aws-network-connector:"


# Define a caught API task, retaining prior state and routing failures without reruns.
def task(operation, parameters, result, next_state, failure="JobFailed"):
    return {
        "Type": "Task",
        "Resource": SDK + operation,
        "Parameters": parameters,
        "ResultPath": result,
        "Next": next_state,
        "Catch": [
            {"ErrorEquals": ["States.ALL"], "ResultPath": "$.failure", "Next": failure}
        ],
    }


# Guard JSONPath existence/type before accessing a string in choices or tasks.
def string(path, value=None):
    conditions = [
        {"Variable": path, "IsPresent": True},
        {"Variable": path, "IsString": True},
    ]
    if value is not None:
        conditions.append({"Variable": path, "StringEquals": value})
    else:
        conditions.append({"Not": {"Variable": path, "StringEquals": ""}})
    return {"And": conditions}


# Define one pinned-image launch, one JWE, strict status polling and confirmed cleanup.
def definition(image_arn, image_version, runtime_role, connection_arn, log_group):
    states = {
        "Input": {"Type": "Pass", "Parameters": {"job.$": "$"}, "Next": "InspectImage"},
        "InspectImage": task(
            "getMicrovmImage", {"ImageIdentifier": image_arn}, "$.image", "ImageReady"
        ),
        "ImageReady": {
            "Type": "Choice",
            "Choices": [
                {
                    "Or": [
                        string("$.image.State", "CREATED"),
                        string("$.image.State", "UPDATED"),
                    ],
                    "Next": "InspectVersion",
                }
            ],
            "Default": "JobFailed",
        },
        "InspectVersion": task(
            "getMicrovmImageVersion",
            {"ImageIdentifier": image_arn, "ImageVersion": image_version},
            "$.version",
            "VersionReady",
        ),
        "VersionReady": {
            "Type": "Choice",
            "Choices": [
                {
                    "And": [
                        string("$.version.State", "SUCCESSFUL"),
                        string("$.version.Status", "ACTIVE"),
                    ],
                    "Next": "Launch",
                }
            ],
            "Default": "JobFailed",
        },
        "Launch": task(
            "runMicrovm",
            {
                "ImageIdentifier": image_arn,
                "ImageVersion": image_version,
                "ExecutionRoleArn": runtime_role,
                "ClientToken.$": "$$.Execution.Name",
                "RunHookPayload.$": "States.JsonToString($.job)",
                "IngressNetworkConnectors": [CONNECTOR + "ALL_INGRESS"],
                "EgressNetworkConnectors": [CONNECTOR + "INTERNET_EGRESS"],
                "Logging": {"CloudWatch": {"LogGroup": log_group}},
                "MaximumDurationInSeconds": 1800,
                "IdlePolicy": {
                    "MaxIdleDurationSeconds": 3600,
                    "SuspendedDurationSeconds": 0,
                    "AutoResumeEnabled": False,
                },
            },
            "$.vm",
            "KnownVM",
        ),
        "KnownVM": {
            "Type": "Choice",
            "Choices": [{**string("$.vm.MicrovmId"), "Next": "Endpoint"}],
            "Default": "JobFailed",
        },
        "Endpoint": {
            "Type": "Choice",
            "Choices": [{**string("$.vm.Endpoint"), "Next": "ReadyWait"}],
            "Default": "Error",
        },
        "ReadyWait": {"Type": "Wait", "Seconds": 5, "Next": "Readiness"},
        "Readiness": task(
            "getMicrovm",
            {"MicrovmIdentifier.$": "$.vm.MicrovmId"},
            "$.lifecycle",
            "Running",
            "Error",
        ),
        "Running": {
            "Type": "Choice",
            "Choices": [
                {**string("$.lifecycle.State", "RUNNING"), "Next": "Token"},
                {**string("$.lifecycle.State", "PENDING"), "Next": "ReadyWait"},
            ],
            "Default": "Error",
        },
        "Token": task(
            "createMicrovmAuthToken",
            {
                "MicrovmIdentifier.$": "$.vm.MicrovmId",
                "ExpirationInMinutes": 30,
                "AllowedPorts": [{"Port": 8080}],
            },
            "$.auth",
            "TokenPresent",
            "Error",
        ),
        "TokenPresent": {
            "Type": "Choice",
            "Choices": [
                {**string("$.auth.AuthToken['X-aws-proxy-auth']"), "Next": "Poll"}
            ],
            "Default": "Error",
        },
        "Poll": {
            "Type": "Task",
            "Resource": "arn:aws:states:::http:invoke",
            "Parameters": {
                "ApiEndpoint.$": "States.Format('https://{}/status', $.vm.Endpoint)",
                "Method": "GET",
                "Headers": {
                    "X-aws-proxy-auth.$": "$.auth.AuthToken['X-aws-proxy-auth']",
                    "X-aws-proxy-port": "8080",
                },
                "InvocationConfig": {"ConnectionArn": connection_arn},
            },
            "TimeoutSeconds": 10,
            "ResultPath": "$.response",
            "Next": "ResponsePresent",
            "Catch": [
                {
                    "ErrorEquals": ["States.ALL"],
                    "ResultPath": "$.failure",
                    "Next": "Error",
                }
            ],
        },
        "ResponsePresent": {
            "Type": "Choice",
            "Choices": [
                {
                    "And": [
                        {"Variable": "$.response.StatusCode", "IsPresent": True},
                        {"Variable": "$.response.StatusCode", "NumericEquals": 200},
                        {"Variable": "$.response.ResponseBody", "IsPresent": True},
                    ],
                    "Next": "EncodeStatus",
                }
            ],
            "Default": "Error",
        },
        "EncodeStatus": {
            "Type": "Pass",
            "Parameters": {"body.$": "States.JsonToString($.response.ResponseBody)"},
            "ResultPath": "$.status",
            "Next": "Status",
        },
        "Status": {
            "Type": "Choice",
            "Choices": [
                {
                    "Variable": "$.status.body",
                    "StringEquals": '{"status":"pending"}',
                    "Next": "PendingWait",
                },
                {
                    "Variable": "$.status.body",
                    "StringEquals": '{"status":"ok"}',
                    "Next": "OK",
                },
            ],
            "Default": "Error",
        },
        "PendingWait": {"Type": "Wait", "Seconds": 30, "Next": "Poll"},
        "OK": {
            "Type": "Pass",
            "Result": "ok",
            "ResultPath": "$.outcome",
            "Next": "Terminate",
        },
        "Error": {
            "Type": "Pass",
            "Result": "error",
            "ResultPath": "$.outcome",
            "Next": "Terminate",
        },
        # CDK drops None values; retain the acknowledgment separately so the VM ID survives.
        "Terminate": task(
            "terminateMicrovm",
            {"MicrovmIdentifier.$": "$.vm.MicrovmId"},
            "$.terminationRequest",
            "CleanupWait",
            "CleanupFailed",
        ),
        "CleanupWait": {"Type": "Wait", "Seconds": 5, "Next": "Confirm"},
        "Confirm": task(
            "getMicrovm",
            {"MicrovmIdentifier.$": "$.vm.MicrovmId"},
            "$.lifecycle",
            "Terminated",
            "CleanupFailed",
        ),
        "Terminated": {
            "Type": "Choice",
            "Choices": [
                {**string("$.lifecycle.State", "TERMINATED"), "Next": "PublicResult"},
                {**string("$.lifecycle.State", "TERMINATING"), "Next": "CleanupWait"},
            ],
            "Default": "CleanupFailed",
        },
        "PublicResult": {
            "Type": "Pass",
            "Parameters": {
                "microvmId.$": "$.vm.MicrovmId",
                "status.$": "$.outcome",
                "termination": "TERMINATED",
            },
            "Next": "Outcome",
        },
        "Outcome": {
            "Type": "Choice",
            "Choices": [
                {"Variable": "$.status", "StringEquals": "ok", "Next": "Success"}
            ],
            "Default": "JobFailed",
        },
        "Success": {"Type": "Succeed"},
        "JobFailed": {
            "Type": "Fail",
            "Error": "JobFailed",
            "Cause": "Task failed; inspect CloudWatch and retained VM ID. Unknown launches remain lifespan-bounded.",
        },
        "CleanupFailed": {
            "Type": "Fail",
            "Error": "CleanupUnconfirmed",
            "Cause": "Termination is unconfirmed; recover the retained VM ID manually. VM lifespan remains the fallback.",
        },
    }
    return {
        "Comment": "One VM/Codex invocation; timeout/StopExecution rely on VM lifespan and manual recovery.",
        "StartAt": "Input",
        "TimeoutSeconds": 1680,
        "States": states,
    }

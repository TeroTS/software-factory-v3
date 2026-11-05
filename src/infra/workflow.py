# Connect one Standard Job lifecycle using deployment pins; terminal results exclude tokens.
from aws_cdk import Duration
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks

CONNECTOR = "arn:aws:lambda:us-east-1:aws:network-connector:aws-network-connector:"


# Build a supported SDK task, preserving prior state and granting only the pinned image scope.
def task(scope, name, operation, parameters, result, image_arn):
    return tasks.CallAwsService(
        scope,
        name,
        service="lambdamicrovms",
        action=operation,
        iam_action="lambda:" + operation[0].upper() + operation[1:],
        iam_resources=[image_arn],
        parameters=parameters,
        result_path=result,
    )


# Guard absent/nonstring values before string comparisons in lifecycle/status decisions.
def string(path, value=None):
    return sfn.Condition.and_(
        sfn.Condition.is_present(path),
        sfn.Condition.is_string(path),
        sfn.Condition.string_equals(path, value)
        if value is not None
        else sfn.Condition.not_(sfn.Condition.string_equals(path, "")),
    )


# Use deployment pins to validate/launch/poll/terminate one Job; queued success persists to the supplied table after cleanup.
def definition(
    scope,
    image_arn,
    image_version,
    runtime_role,
    connection_arn,
    log_group,
    table_name,
    table_arn,
):
    # Direct tests retain [Job]; the Dispatcher carries queue keys separately from the worker payload.
    submission = sfn.Choice(
        scope, "CheckSubmission", query_language=sfn.QueryLanguage.JSONATA
    )
    invalid = sfn.Fail(
        scope,
        "InvalidSubmission",
        error="InvalidSubmission",
        cause="Workflow input must be an array containing exactly one Job object.",
    )
    start = sfn.Pass(scope, "Input", parameters={"job.$": "$[0]"})
    submission.when(
        sfn.Condition.jsonata(
            "{% $type($states.input) = 'array' and $count($states.input) = 1 "
            "and $type($states.input[0]) = 'object' %}"
        ),
        start,
    )
    queued_start = sfn.Pass(
        scope, "QueuedInput", parameters={"job.$": "$.jobs[0]", "queue.$": "$.queue"}
    )
    submission.when(
        sfn.Condition.jsonata(
            "{% $type($states.input) = 'object' and $type($states.input.jobs) = 'array' "
            "and $count($states.input.jobs) = 1 and $type($states.input.jobs[0]) = 'object' "
            "and $type($states.input.queue) = 'object' and $states.input.queue.PK = 'JOBS' "
            "and $type($states.input.queue.SK) = 'string' and $states.input.queue.SK != '' %}"
        ),
        queued_start,
    ).otherwise(invalid)
    failed = sfn.Fail(scope, "JobFailed", error_path="$.error", cause_path="$.cause")
    succeeded = sfn.Succeed(scope, "JobSucceeded")
    no_vm = sfn.Pass(
        scope,
        "RecordLaunchFailure",
        parameters={
            "status": "error",
            "error": "JobFailed",
            "cause": "Launch failed without a known VM ID; uncertain launches remain lifespan-bounded. Inspect execution history and recover manually.",
        },
    ).next(failed)
    cleanup_failed = sfn.Pass(
        scope,
        "RecordCleanupFailure",
        parameters={
            "microvmId.$": "$.vm.MicrovmId",
            "status": "error",
            "error": "CleanupUnconfirmed",
            "cause": "Termination is unconfirmed; recover the retained VM ID manually. VM lifespan remains the fallback.",
        },
    ).next(failed)
    job_failed = sfn.Pass(
        scope,
        "RecordJobFailure",
        parameters={
            "microvmId.$": "$.microvmId",
            "status.$": "$.status",
            "termination.$": "$.termination",
            "error": "JobFailed",
            "cause": "Task failed; inspect CloudWatch and the retained VM ID.",
        },
    ).next(failed)
    success = sfn.Pass(
        scope,
        "RecordSuccess",
        result=sfn.Result.from_object({"status": "ok"}),
        result_path="$.outcome",
    )
    failure = sfn.Pass(
        scope,
        "RecordFailure",
        parameters={
            "status": "error",
            "reason": "Worker or post-launch service failure; inspect CloudWatch and execution history.",
        },
        result_path="$.outcome",
    )
    launch = task(
        scope,
        "LaunchVM",
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
        image_arn,
    )
    check_launch = sfn.Choice(scope, "CheckLaunch")
    check_endpoint = sfn.Choice(scope, "CheckEndpoint")
    startup_wait = sfn.Wait(
        scope, "WaitForVMToStart", time=sfn.WaitTime.duration(Duration.seconds(5))
    )
    get_vm = task(
        scope,
        "GetVMState",
        "getMicrovm",
        {"MicrovmIdentifier.$": "$.vm.MicrovmId"},
        "$.lifecycle",
        image_arn,
    )
    running = sfn.Choice(scope, "IsVMRunning")
    token = task(
        scope,
        "GetWorkerToken",
        "createMicrovmAuthToken",
        {
            "MicrovmIdentifier.$": "$.vm.MicrovmId",
            "ExpirationInMinutes": 30,
            "AllowedPorts": [{"Port": 8080}],
        },
        "$.auth",
        image_arn,
    )
    check_token = sfn.Choice(scope, "CheckWorkerToken")
    # HttpInvoke grants wildcard state-machine access and couples apiRoot to the URL;
    # a raw HTTP task preserves the dynamic host and the stack's narrower GET permissions.
    poll = sfn.CustomState(
        scope,
        "PollWorker",
        state_json={
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
        },
    )
    response_present = sfn.Choice(scope, "CheckWorkerResponse")
    encode_status = sfn.Pass(
        scope,
        "EncodeStatus",
        parameters={"body.$": "States.JsonToString($.response.ResponseBody)"},
        result_path="$.status",
    )
    status = sfn.Choice(scope, "CheckWorkerStatus")
    worker_wait = sfn.Wait(
        scope, "WaitForWorker", time=sfn.WaitTime.duration(Duration.seconds(10))
    )
    terminate = task(
        scope,
        "TerminateVM",
        "terminateMicrovm",
        {"MicrovmIdentifier.$": "$.vm.MicrovmId"},
        "$.terminationRequest",
        image_arn,
    )
    termination_wait = sfn.Wait(
        scope, "WaitForVMToTerminate", time=sfn.WaitTime.duration(Duration.seconds(5))
    )
    get_termination = task(
        scope,
        "GetTerminationState",
        "getMicrovm",
        {"MicrovmIdentifier.$": "$.vm.MicrovmId"},
        "$.lifecycle",
        image_arn,
    )
    terminated = sfn.Choice(scope, "IsVMTerminated")
    result = sfn.Pass(
        scope,
        "EndResult",
        parameters={
            "microvmId.$": "$.vm.MicrovmId",
            "status.$": "$.outcome.status",
            "termination": "TERMINATED",
        },
    )
    outcome = sfn.Choice(scope, "CheckEndResult")
    complete = tasks.CallAwsService(
        scope,
        "CompleteQueuedJob",
        service="dynamodb",
        action="updateItem",
        iam_action="dynamodb:UpdateItem",
        iam_resources=[table_arn],
        parameters={
            "TableName": table_name,
            "Key": {"PK": {"S.$": "$.queue.PK"}, "SK": {"S.$": "$.queue.SK"}},
            "UpdateExpression": "SET #status = :status",
            "ExpressionAttributeNames": {"#status": "status"},
            "ExpressionAttributeValues": {":status": {"S": "COMPLETED"}},
        },
        result_path=sfn.JsonPath.DISCARD,
    )
    check_queue = sfn.Choice(scope, "CheckQueueCompletion")
    check_queue.when(
        sfn.Condition.and_(
            string("$.outcome.status", "ok"), sfn.Condition.is_present("$.queue")
        ),
        complete,
    ).otherwise(result)
    complete.next(result)

    launch.add_catch(no_vm, result_path="$.failure")
    for api in (get_vm, token, poll):
        api.add_catch(failure, result_path="$.failure")
    for api in (terminate, get_termination):
        api.add_catch(cleanup_failed, result_path="$.failure")

    queued_start.next(launch)
    start.next(launch).next(check_launch)
    check_launch.when(string("$.vm.MicrovmId"), check_endpoint).otherwise(no_vm)
    check_endpoint.when(string("$.vm.Endpoint"), startup_wait).otherwise(failure)
    startup_wait.next(get_vm).next(running)
    running.when(string("$.lifecycle.State", "RUNNING"), token)
    running.when(string("$.lifecycle.State", "PENDING"), startup_wait).otherwise(
        failure
    )
    token.next(check_token)
    check_token.when(string("$.auth.AuthToken['X-aws-proxy-auth']"), poll).otherwise(
        failure
    )
    poll.next(response_present)
    response_present.when(
        sfn.Condition.and_(
            sfn.Condition.is_present("$.response.StatusCode"),
            sfn.Condition.is_numeric("$.response.StatusCode"),
            sfn.Condition.number_equals("$.response.StatusCode", 200),
            sfn.Condition.is_present("$.response.ResponseBody"),
        ),
        encode_status,
    ).otherwise(failure)
    encode_status.next(status)
    status.when(string("$.status.body", '{"status":"pending"}'), worker_wait)
    status.when(string("$.status.body", '{"status":"ok"}'), success).otherwise(failure)
    worker_wait.next(poll)
    success.next(terminate)
    failure.next(terminate)
    terminate.next(termination_wait).next(get_termination).next(terminated)
    terminated.when(string("$.lifecycle.State", "TERMINATED"), check_queue)
    terminated.when(
        string("$.lifecycle.State", "TERMINATING"), termination_wait
    ).otherwise(cleanup_failed)
    result.next(outcome)
    outcome.when(string("$.status", "ok"), succeeded).otherwise(job_failed)
    return submission

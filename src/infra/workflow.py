# Run one job on a VM using the configured image version; keep tokens out of the final result.
from aws_cdk import Duration, Stack
from aws_cdk import aws_stepfunctions as sfn
from aws_cdk import aws_stepfunctions_tasks as tasks


# Resolve a managed network connector in the supplied construct's deployment Region.
def network_connector(scope, name):
    return f"arn:aws:lambda:{Stack.of(scope).region}:aws:network-connector:aws-network-connector:{name}"


# Create an AWS call that saves its reply at result and can access only image_arn.
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


# Check that a field is a string and matches value, or is nonempty if no value is given.
def string_matches(path, value=None):
    return sfn.Condition.and_(
        sfn.Condition.is_present(path),
        sfn.Condition.is_string(path),
        sfn.Condition.string_equals(path, value)
        if value is not None
        else sfn.Condition.not_(sfn.Condition.string_equals(path, "")),
    )


# Build the job's workflow using the supplied image and AWS resources.
# Stop the VM before marking a queued job complete in the supplied table.
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
    # Check whether the job was submitted directly or came from the queue.
    submission = sfn.Choice(
        scope, "CheckSubmission", query_language=sfn.QueryLanguage.JSONATA
    )
    # Reject invalid input before starting a VM.
    invalid = sfn.Fail(
        scope,
        "InvalidSubmission",
        error="InvalidSubmission",
        cause="Workflow input must be an array containing exactly one Job object.",
    )
    # Take the single job from the input list.
    direct_input = sfn.Pass(scope, "Input", parameters={"job.$": "$[0]"})
    # Keep the queue keys so we can mark this job complete later.
    queued_input = sfn.Pass(
        scope, "QueuedInput", parameters={"job.$": "$.jobs[0]", "queue.$": "$.queue"}
    )
    # Direct input must be a list containing one job object.
    submission.when(
        sfn.Condition.jsonata(
            "{% $type($states.input) = 'array' and $count($states.input) = 1 "
            "and $type($states.input[0]) = 'object' %}"
        ),
        direct_input,
    )
    # Queued input must contain one job and valid queue keys.
    submission.when(
        sfn.Condition.jsonata(
            "{% $type($states.input) = 'object' and $type($states.input.jobs) = 'array' "
            "and $count($states.input.jobs) = 1 and $type($states.input.jobs[0]) = 'object' "
            "and $type($states.input.queue) = 'object' and $states.input.queue.PK = 'JOBS' "
            "and $type($states.input.queue.SK) = 'string' and $states.input.queue.SK != '' %}"
        ),
        queued_input,
    ).otherwise(invalid)

    # If we don't know the VM ID, report the launch failure without trying to stop it.
    launch_failure = sfn.Pass(
        scope,
        "RecordLaunchFailure",
        parameters={
            "status": "error",
            "error": "JobFailed",
            "cause": "Launch failed without a known VM ID; uncertain launches remain lifespan-bounded. Inspect execution history and recover manually.",
        },
    )
    # Start the chosen image version; limit VM life to 30 minutes.
    # Use the execution name to prevent duplicate launches for this run.
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
            "IngressNetworkConnectors": [network_connector(scope, "ALL_INGRESS")],
            "EgressNetworkConnectors": [network_connector(scope, "INTERNET_EGRESS")],
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
    # Check that AWS returned a VM ID.
    check_launch = sfn.Choice(scope, "CheckLaunch")
    # Check that AWS returned the VM's address.
    check_endpoint = sfn.Choice(scope, "CheckEndpoint")
    # Wait five seconds between startup checks.
    startup_wait = sfn.Wait(
        scope, "WaitForVMToStart", time=sfn.WaitTime.duration(Duration.seconds(5))
    )
    # Ask AWS whether the VM has started; keep the VM ID and address.
    get_vm = task(
        scope,
        "GetVMState",
        "getMicrovm",
        {"MicrovmIdentifier.$": "$.vm.MicrovmId"},
        "$.lifecycle",
        image_arn,
    )
    # Wait if the VM is still starting; contact the worker once it is running.
    check_running = sfn.Choice(scope, "IsVMRunning")

    # Without a VM ID, we can't safely request termination.
    launch.add_catch(launch_failure, result_path="$.failure")
    launch.next(check_launch)
    check_launch.when(string_matches("$.vm.MicrovmId"), check_endpoint).otherwise(
        launch_failure
    )
    # Ask AWS again after each wait.
    startup_wait.next(get_vm).next(check_running)

    # Get one access token for the worker's port; reuse it for all status checks.
    get_worker_token = task(
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
    # Check that the access token is present and not empty.
    check_token = sfn.Choice(scope, "CheckWorkerToken")
    # Call the worker's /status URL using the access token.
    # Define this HTTP call directly: CDK's HttpInvoke would broaden permissions
    # and tie the URL to a fixed address, but each VM has its own address.
    poll_worker = sfn.CustomState(
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
    # Check that the worker replied with HTTP 200 and a body.
    check_response = sfn.Choice(scope, "CheckWorkerResponse")
    # Convert the reply body to a string so we can check its exact contents.
    encode_status = sfn.Pass(
        scope,
        "EncodeStatus",
        parameters={"body.$": "States.JsonToString($.response.ResponseBody)"},
        result_path="$.status",
    )
    # Accept pending or independently confirmed GitHub merged PR state (ok); any other reply fails.
    check_status = sfn.Choice(scope, "CheckWorkerStatus")
    # Wait ten seconds before checking the worker again.
    worker_wait = sfn.Wait(
        scope, "WaitForWorker", time=sfn.WaitTime.duration(Duration.seconds(10))
    )
    # Save success, but keep the VM ID so we can stop it first.
    record_success = sfn.Pass(
        scope,
        "RecordSuccess",
        result=sfn.Result.from_object({"status": "ok"}),
        result_path="$.outcome",
    )
    # Save a failure message without including AWS errors or access tokens.
    record_failure = sfn.Pass(
        scope,
        "RecordFailure",
        parameters={
            "status": "error",
            "reason": "Worker or post-launch service failure; inspect CloudWatch and execution history.",
        },
        result_path="$.outcome",
    )

    # If getting a token or calling the worker fails, record failure.
    for api in (get_worker_token, poll_worker):
        api.add_catch(record_failure, result_path="$.failure")
    # Check the token before calling the worker, then check its reply.
    get_worker_token.next(check_token)
    check_token.when(
        string_matches("$.auth.AuthToken['X-aws-proxy-auth']"), poll_worker
    ).otherwise(record_failure)
    poll_worker.next(check_response)
    check_response.when(
        sfn.Condition.and_(
            sfn.Condition.is_present("$.response.StatusCode"),
            sfn.Condition.is_numeric("$.response.StatusCode"),
            sfn.Condition.number_equals("$.response.StatusCode", 200),
            sfn.Condition.is_present("$.response.ResponseBody"),
        ),
        encode_status,
    ).otherwise(record_failure)
    # Keep checking while pending; otherwise record success or failure.
    encode_status.next(check_status)
    check_status.when(
        string_matches("$.status.body", '{"status":"pending"}'), worker_wait
    )
    check_status.when(
        string_matches("$.status.body", '{"status":"ok"}'), record_success
    ).otherwise(record_failure)
    worker_wait.next(poll_worker)

    # If we can't confirm the VM has stopped, save its ID for manual recovery.
    cleanup_failure = sfn.Pass(
        scope,
        "RecordCleanupFailure",
        parameters={
            "microvmId.$": "$.vm.MicrovmId",
            "status": "error",
            "error": "CleanupUnconfirmed",
            "cause": "Termination is unconfirmed; recover the retained VM ID manually. VM lifespan remains the fallback.",
        },
    )
    # Ask AWS to stop the VM; keep the job result and queue keys.
    terminate = task(
        scope,
        "TerminateVM",
        "terminateMicrovm",
        {"MicrovmIdentifier.$": "$.vm.MicrovmId"},
        "$.terminationRequest",
        image_arn,
    )
    # Wait five seconds before checking whether the VM has stopped.
    termination_wait = sfn.Wait(
        scope, "WaitForVMToTerminate", time=sfn.WaitTime.duration(Duration.seconds(5))
    )
    # Ask AWS whether the VM really stopped; the stop request alone isn't proof.
    get_termination = task(
        scope,
        "GetTerminationState",
        "getMicrovm",
        {"MicrovmIdentifier.$": "$.vm.MicrovmId"},
        "$.lifecycle",
        image_arn,
    )
    # Continue when the VM has stopped, or wait if it is still stopping.
    check_terminated = sfn.Choice(scope, "IsVMTerminated")

    # If either AWS call fails, save the VM ID for manual recovery.
    for api in (terminate, get_termination):
        api.add_catch(cleanup_failure, result_path="$.failure")
    terminate.next(termination_wait).next(get_termination).next(check_terminated)

    # Mark a queued Job complete only after confirmed GitHub merged PR state and confirmed VM termination.
    complete_queued_job = tasks.CallAwsService(
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
    # Skip the queue update for failed jobs or jobs submitted directly.
    check_queue = sfn.Choice(scope, "CheckQueueCompletion")
    # Return only the VM ID, job status, and proof that the VM stopped.
    terminal_result = sfn.Pass(
        scope,
        "EndResult",
        parameters={
            "microvmId.$": "$.vm.MicrovmId",
            "status.$": "$.outcome.status",
            "termination": "TERMINATED",
        },
    )
    # Finish the workflow as successful or failed based on the job's result.
    check_outcome = sfn.Choice(scope, "CheckEndResult")
    # Add a failure reason; keep the VM ID and confirmation that it stopped.
    job_failure = sfn.Pass(
        scope,
        "RecordJobFailure",
        parameters={
            "microvmId.$": "$.microvmId",
            "status.$": "$.status",
            "termination.$": "$.termination",
            "error": "JobFailed",
            "cause": "Task failed; inspect CloudWatch and the retained VM ID.",
        },
    )
    # End the workflow with the saved error and reason.
    failed = sfn.Fail(scope, "JobFailed", error_path="$.error", cause_path="$.cause")
    # Report success only after the VM stops and any queue update succeeds.
    succeeded = sfn.Succeed(scope, "JobSucceeded")

    # Update the queue before returning the result; if the update fails, fail the workflow.
    check_queue.when(
        sfn.Condition.and_(
            string_matches("$.outcome.status", "ok"),
            sfn.Condition.is_present("$.queue"),
        ),
        complete_queued_job,
    ).otherwise(terminal_result)
    complete_queued_job.next(terminal_result)
    terminal_result.next(check_outcome)
    check_outcome.when(string_matches("$.status", "ok"), succeeded).otherwise(
        job_failure
    )

    # Start the same VM workflow for direct and queued jobs.
    direct_input.next(launch)
    queued_input.next(launch)
    # No VM ID means we must fail without a stop request.
    # If we have an ID but no address, record failure and stop the VM.
    launch_failure.next(failed)
    check_endpoint.when(string_matches("$.vm.Endpoint"), startup_wait).otherwise(
        record_failure
    )
    # If startup fails or the VM enters an unexpected state, record failure.
    get_vm.add_catch(record_failure, result_path="$.failure")
    check_running.when(string_matches("$.lifecycle.State", "RUNNING"), get_worker_token)
    check_running.when(
        string_matches("$.lifecycle.State", "PENDING"), startup_wait
    ).otherwise(record_failure)
    # Stop the VM whether the worker succeeded or failed.
    record_success.next(terminate)
    record_failure.next(terminate)
    # Once the VM has stopped, finish the job. Keep waiting if it is stopping.
    check_terminated.when(
        string_matches("$.lifecycle.State", "TERMINATED"), check_queue
    )
    check_terminated.when(
        string_matches("$.lifecycle.State", "TERMINATING"), termination_wait
    ).otherwise(cleanup_failure)
    # Fail the workflow if the job failed or we couldn't confirm the VM stopped.
    cleanup_failure.next(failed)
    job_failure.next(failed)
    return submission

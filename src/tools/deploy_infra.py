# Deploy the native lifecycle workflow at an explicitly verified image pin; never start a Job.
from src.tools import live


# Review/deploy only orchestration, then validate actual definition, connection and source pin.
def main(argv=None):
    parser = live.parser(
        "Deploy workflow infrastructure using an explicit verified image version"
    )
    parser.add_argument("--version", required=True)
    args = parser.parse_args(argv)
    live.local("make", "synth")
    session = live.operator(args.profile)
    live.idle(session)
    live.verify_source(session, args.version)
    # This syntactically valid connection is validation-only; CDK supplies the real resource ARN.
    validation_connection = f"arn:aws:events:{live.REGION}:{live.ACCOUNT}:connection/validation-only/00000000-0000-0000-0000-000000000000"
    live.validate_definition(
        session, live.workflow_definition(session, args.version, validation_connection)
    )
    parameter = "FactoryWorkflow:ImageVersion=" + args.version
    live.cdk(
        session, "diff", "FactoryWorkflow", "--exclusively", "--parameters", parameter
    )
    live.confirm(
        "Review workflow/role/trust diff above. Deploy orchestration at image "
        + args.version
        + "?"
    )
    live.idle(session)
    live.verify_source(session, args.version)
    live.cdk(
        session,
        "deploy",
        "FactoryWorkflow",
        "--exclusively",
        "--require-approval",
        "never",
        "--parameters",
        parameter,
    )
    live.verify_workflow(session, args.version)
    print("Verified ACTIVE/Standard current-source workflow at image " + args.version)
    print(
        "No Job started. Prepare a reviewed Job file and retain one explicit execution name."
    )


if __name__ == "__main__":
    raise SystemExit(live.run(main))

# Validate the current ARM64 source, review native CDK changes and deploy only storage/image resources.
from src.tools import live


# Run local gates before approval; stop on failed deployment or mismatched active image source.
def main(argv=None):
    args = live.parser(
        "Validate and deploy the current image; no Job or workflow deployment"
    ).parse_args(argv)
    live.local("make", "verify", "preflight", "synth")
    session = live.operator(args.profile)
    live.idle(session)
    live.cdk(session, "diff", "FactoryArtifacts", "FactoryImage", "--no-change-set")
    live.confirm(
        "Review source, role/trust policies and diff above. Deploy artifacts/image (billable build; no secret replacement or permission broadening outside the reviewed diff)?"
    )
    live.idle(session)
    live.cdk(
        session,
        "deploy",
        "FactoryArtifacts",
        "--exclusively",
        "--require-approval",
        "never",
    )
    live.cdk(
        session,
        "deploy",
        "FactoryImage",
        "--exclusively",
        "--require-approval",
        "never",
        "--no-rollback",
    )
    version = live.stack_outputs(session, "FactoryImage")["LatestActiveVersion"]
    live.verify_source(session, version)
    print("Verified current-source SUCCESSFUL/ACTIVE image version: " + version)
    print(
        "Next: uv run --locked python -m src.tools.deploy_infra --version "
        + version
        + " --profile "
        + args.profile
    )


if __name__ == "__main__":
    raise SystemExit(live.run(main))

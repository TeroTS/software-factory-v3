# Verify agent command chronology and preserved source/tests/publication; reject false success.
import ast
import shlex

TEST_COMMAND = "./scripts/verify"


# Extract the actual standalone shell command from Codex's command-execution event.
def command_words(command):
    words = shlex.split(command)
    if len(words) == 3 and words[1] in ("-lc", "-c"):
        words = shlex.split(words[2])
    return words


# Require a successful sandbox probe, final tests before commit/push/PR, and no agent error.
def verify_events(events):
    commands = []
    for event in events:
        if event.get("type") in ("error", "turn.failed"):
            raise ValueError("Agent inference or execution failed")
        item = event.get("item", {})
        if (
            event.get("type") == "item.completed"
            and item.get("type") == "command_execution"
        ):
            commands.append(item)
    if (
        not commands
        or commands[0].get("exit_code") != 0
        or "gate2-shell-ok" not in commands[0].get("aggregated_output", "")
    ):
        raise ValueError("Initial sandbox shell did not pass")
    tested = committed = pushed = published = False
    push_attempted = pr_attempted = False
    for item in commands[1:]:
        words = command_words(item.get("command", ""))
        if words == shlex.split(TEST_COMMAND):
            tested = item.get("exit_code") == 0
        elif (
            tested
            and not pushed
            and words[:2]
            not in (
                ["git", "add"],
                ["git", "diff"],
                ["git", "status"],
                ["git", "commit"],
                ["git", "push"],
                ["git", "ls-remote"],
                ["git", "show"],
                ["git", "log"],
                ["git", "rev-parse"],
            )
        ):
            tested = False
        if (
            push_attempted
            and words[:2] == ["git", "ls-remote"]
            and item.get("exit_code") == 0
        ):
            pushed = True
        if (
            pr_attempted
            and words[:3] in (["gh", "pr", "view"], ["gh", "pr", "list"])
            and item.get("exit_code") == 0
        ):
            published = True
        for prefix, stage in (
            (["git", "commit"], "commit"),
            (["git", "push"], "push"),
            (["gh", "pr", "create"], "pr"),
        ):
            if words[: len(prefix)] != prefix:
                continue
            if (
                not tested
                or (stage == "push" and not committed)
                or (stage == "pr" and not pushed)
            ):
                raise ValueError("Publication preceded approved passing tests")
            if stage == "push":
                push_attempted = True
            elif stage == "pr":
                pr_attempted = True
            if item.get("exit_code") == 0:
                if stage == "commit":
                    committed = True
                elif stage == "push":
                    pushed = True
                else:
                    published = True
    if not (tested and committed and pushed and published):
        raise ValueError("Missing successful final tests or agent publication commands")


# Preserve baseline functions/class tests by AST, permitting new methods and extended imports.
def preserve_nodes(before, after):
    for node in before:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            candidates = [
                item
                for item in after
                if type(item) is type(node)
                and getattr(item, "module", None) == getattr(node, "module", None)
            ]
            aliases = {ast.dump(alias) for item in candidates for alias in item.names}
            if not {ast.dump(alias) for alias in node.names} <= aliases:
                raise ValueError("Baseline import removed")
        elif isinstance(node, ast.ClassDef):
            candidate = next(
                (
                    item
                    for item in after
                    if isinstance(item, ast.ClassDef) and item.name == node.name
                ),
                None,
            )
            if candidate is None or [
                ast.dump(item)
                for item in node.bases + node.keywords + node.decorator_list
            ] != [
                ast.dump(item)
                for item in candidate.bases
                + candidate.keywords
                + candidate.decorator_list
            ]:
                raise ValueError("Baseline test class changed")
            preserve_nodes(node.body, candidate.body)
        elif ast.dump(node) not in {ast.dump(item) for item in after}:
            raise ValueError("Baseline behavior or tests changed")


# Require meaningful semantic changes in both source and tests without baseline weakening.
def verify_source(baseline, current):
    if set(baseline) != set(current):
        raise ValueError("Unexpected changed paths")
    for path in baseline:
        before, after = ast.parse(baseline[path]), ast.parse(current[path])
        if ast.dump(before) == ast.dump(after):
            raise ValueError("Missing meaningful source/test change")
        preserve_nodes(before.body, after.body)
        if path.startswith("tests/"):
            # unittest identities include the class; different classes may reuse method names.
            original = {
                (parent.name, node.name)
                for parent in ast.walk(before)
                if isinstance(parent, ast.ClassDef)
                for node in parent.body
                if isinstance(node, ast.FunctionDef)
            }
            added = {
                (parent.name, node.name)
                for parent in ast.walk(after)
                if isinstance(parent, ast.ClassDef)
                for node in parent.body
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
            } - original
            if not added:
                raise ValueError("No added tests")


# Check one open, unmerged PR against verified Git branch/head, pinned base, and configured target.
def verify_pr(prs, branch, head, base, target):
    if len(prs) != 1:
        raise ValueError("Expected exactly one PR on task branch")
    pr = prs[0]
    expected = {
        "state": "OPEN",
        "mergedAt": None,
        "baseRefName": target,
        "headRefName": branch,
        "headRefOid": head,
        "baseRefOid": base,
    }
    if (
        not isinstance(pr.get("number"), int)
        or pr["number"] <= 0
        or any(pr.get(key) != value for key, value in expected.items())
    ):
        raise ValueError("PR does not match verified unmerged task publication")

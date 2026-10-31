# Check the approved demonstration's behavior independently of agent-authored tests.
import importlib.util
import sys
import unittest
from pathlib import Path


# Load only the supplied repository's calculator and require all approved operand cases.
def verify(repository):
    spec = importlib.util.spec_from_file_location(
        "calculator", Path(repository) / "calculator.py"
    )
    calculator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(calculator)
    for a, b in ((2, 3), (-2, 3), (-2, -3), (0, 3), (3, 0), (0, 0)):
        if calculator.multiply(a, b) != a * b or calculator.add(a, b) != a + b:
            raise ValueError("Approved multiplication/addition behavior failed")
    sys.modules["calculator"] = calculator
    cases = set()

    # Observe actual suite calls, not the independent probes, to prove committed operand tests.
    def observe(frame, event, argument):
        if event == "call" and frame.f_code is calculator.multiply.__code__:
            cases.add((frame.f_locals["a"], frame.f_locals["b"]))

    suite = unittest.TestLoader().discover(str(Path(repository) / "tests"))
    previous = sys.getprofile()
    sys.setprofile(observe)
    try:
        result = unittest.TextTestRunner(stream=sys.stdout, verbosity=2).run(suite)
    finally:
        sys.setprofile(previous)
    categories = (
        any(a > 0 and b > 0 for a, b in cases),
        any(a * b < 0 for a, b in cases),
        any(a < 0 and b < 0 for a, b in cases),
        any(a == 0 and b != 0 for a, b in cases),
        any(b == 0 and a != 0 for a, b in cases),
    )
    if not result.wasSuccessful() or not all(categories):
        raise ValueError(
            "Committed tests do not pass/cover approved operand categories"
        )
    print(
        "Approved behavior and committed positive/negative/zero operand tests passed",
        flush=True,
    )


if __name__ == "__main__":
    verify(sys.argv[1])

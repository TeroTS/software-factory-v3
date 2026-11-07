import json
import tempfile
import unittest
from pathlib import Path

from src.worker.review import ReviewError, read_review_result


class ReviewTests(unittest.TestCase):
    # Validate candidate-bound evidence directly, without starting a worker for every malformed result.
    def test_review_result_accepts_only_consistent_candidate_bound_evidence(self):
        head = "b" * 40
        finding = {
            "axis": "Spec",
            "location": "greeting.txt:1",
            "problem": "Missing greeting",
            "correction": "Add greeting",
        }
        valid = {"candidateHead": head, "verdict": "PASS", "findings": []}
        with tempfile.TemporaryDirectory() as directory:
            result = Path(directory) / "review.json"
            with self.assertRaises(ReviewError):
                read_review_result(result, head)
            result.write_text("not JSON")
            with self.assertRaises(ReviewError):
                read_review_result(result, head)
            for review in (valid, {**valid, "verdict": "FAIL", "findings": [finding]}):
                with self.subTest(review=review):
                    result.write_text(json.dumps(review))
                    self.assertEqual(
                        read_review_result(result, head),
                        (review["verdict"], review["findings"]),
                    )
            for review in (
                [],
                {"verdict": "PASS", "findings": []},
                {**valid, "extra": True},
                {**valid, "candidateHead": "c" * 40},
                {**valid, "verdict": "SKIP"},
                {**valid, "findings": None},
                {**valid, "findings": [finding]},
                {**valid, "verdict": "FAIL"},
                *(
                    {**valid, "verdict": "FAIL", "findings": [invalid]}
                    for invalid in (
                        None,
                        {},
                        {**finding, "extra": True},
                        {**finding, "axis": "Other"},
                        {**finding, "correction": " "},
                        {**finding, "location": 1},
                    )
                ),
            ):
                with self.subTest(review=review), self.assertRaises(ReviewError):
                    result.write_text(json.dumps(review))
                    read_review_result(result, head)


if __name__ == "__main__":
    unittest.main()

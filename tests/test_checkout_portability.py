"""Regression checks for test-suite checkout portability."""

from pathlib import Path


def test_tests_do_not_reference_original_checkout():
    tests_dir = Path(__file__).resolve().parent
    original_checkout = "/home/user/" + "macubuntu"
    offenders = []
    for path in tests_dir.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".md"}:
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if original_checkout in line:
                    offenders.append(f"{path.relative_to(tests_dir)}:{line_number}")
    assert not offenders, "Checkout-specific paths found: " + ", ".join(offenders)

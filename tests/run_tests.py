"""
Zero-dependency test runner: this sandbox has no network access to install
pytest, so this script discovers every `test_*` function in tests/test_*.py
and runs it directly with plain asserts. On a machine with pytest
installed, just run `pytest` instead - these test files are plain
pytest-compatible functions.
"""
import importlib
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

TEST_MODULES = [
    "tests.test_validation",
    "tests.test_extractor",
    "tests.test_storage",
    "tests.test_exporter",
    "tests.test_qualifier",
    "tests.test_ai_extractor",
    "tests.test_html_to_text",
    "tests.test_builtin_templates",
    "tests.test_dedupe",
]


def main() -> int:
    total = 0
    failed = 0
    for mod_name in TEST_MODULES:
        module = importlib.import_module(mod_name)
        test_funcs = [
            getattr(module, name) for name in dir(module)
            if name.startswith("test_") and callable(getattr(module, name))
        ]
        for fn in test_funcs:
            total += 1
            try:
                fn()
                print(f"PASS  {mod_name}.{fn.__name__}")
            except Exception:
                failed += 1
                print(f"FAIL  {mod_name}.{fn.__name__}")
                traceback.print_exc()

    print(f"\n{total - failed}/{total} tests passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

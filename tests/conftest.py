from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def config_path() -> Path:
    return ROOT / "experiments/smoke-mock.yaml"


@pytest.fixture
def grading_path() -> Path:
    return ROOT / "tests/fixtures/grading/smoke-m1/grading.yaml"

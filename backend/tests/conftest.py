from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.dataset import OlistDataset
from backend.app.llm import LLMClient
from backend.app.runtime import DebuggerService


@pytest.fixture(scope="session")
def dataset() -> OlistDataset:
    data = OlistDataset()
    if not data.available:
        pytest.skip("Olist CSVs are not prepared; run scripts/prepare_data.py")
    return data


@pytest.fixture()
def service(tmp_path: Path, dataset: OlistDataset) -> DebuggerService:
    svc = DebuggerService(dataset=dataset, runs_dir=tmp_path / "runs")
    # Tests must never make a provider request, even on a key-bearing machine.
    svc.llm = LLMClient(provider=None, model=None)
    svc.llm.api_key = None
    return svc


@pytest.fixture()
def api(service: DebuggerService):
    from fastapi.testclient import TestClient

    from backend.app.main import create_app

    return TestClient(create_app(service))


def complete_run(service: DebuggerService, run_id: str, max_steps: int = 20) -> dict:
    for _ in range(max_steps):
        view = service.get_view(run_id)
        if view["status"] == "completed":
            return view
        if view["status"] == "failed":
            raise AssertionError(f"run failed: {view['error']}")
        if view["status"] == "awaiting_approval":
            raise AssertionError("run unexpectedly reached an approval boundary")
        service.step_over(run_id)
    raise AssertionError("run did not complete within the step budget")


def wait_until(service: DebuggerService, run_id: str, predicate, timeout: float = 8.0) -> dict:
    deadline = time.time() + timeout
    view = service.get_view(run_id)
    while time.time() < deadline:
        view = service.get_view(run_id)
        if predicate(view):
            return view
        time.sleep(0.03)
    return view

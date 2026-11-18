from __future__ import annotations


def _create(api, task="What is our customer repeat purchase rate?", mode="scripted", dataset="olist"):
    response = api.post("/api/runs", json={"task": task, "mode": mode, "dataset": dataset})
    assert response.status_code == 201, response.text
    return response.json()


def test_api_full_revision_loop(api):
    view = _create(api)
    run_id = view["id"]
    assert api.get(f"/api/runs/{run_id}").json()["id"] == run_id

    command_id = "api-command-step-over-1"
    response = api.post(f"/api/runs/{run_id}/step-over", json={"command_id": command_id})
    assert response.status_code == 200
    first = response.json()
    replay = api.post(f"/api/runs/{run_id}/step-over", json={"command_id": command_id}).json()
    assert replay["idempotent_replay"] is True
    assert replay["cursor"] == first["cursor"]

    for _ in range(8):
        current = api.get(f"/api/runs/{run_id}").json()
        if current["status"] == "completed":
            break
        assert api.post(f"/api/runs/{run_id}/step-over", json={}).status_code == 200
    current = api.get(f"/api/runs/{run_id}").json()
    assert current["status"] == "completed"
    assert current["metrics"]["repeat_purchase_rate"]["formatted"] == "0.0000%"

    step_id = current["steps"][2]["id"]
    response = api.post(
        f"/api/runs/{run_id}/feedback",
        json={
            "step_id": step_id,
            "text": "Use the persistent customer identifier, not the per-order customer record key.",
            "command_id": "api-feedback-1",
        },
    )
    assert response.status_code == 200, response.text
    proposal = response.json()["revisions"][-1]
    assert proposal["status"] == "pending"

    response = api.post(
        f"/api/runs/{run_id}/revisions/{proposal['id']}/accept", json={"command_id": "api-accept-1"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["capabilities"]["replay"] is True

    response = api.post(f"/api/runs/{run_id}/replay", json={})
    assert response.status_code == 200, response.text
    body = response.json()
    child = body["run"]
    assert child["status"] == "completed"
    assert child["metrics"]["repeat_purchase_rate"]["formatted"] == "3.0003%"
    assert body["comparison"]["child_reference_check"]["agreement"] is True

    comparison = api.get(f"/api/runs/{run_id}/compare").json()
    assert comparison["child_run_id"] == child["id"]
    assert comparison["binding_diff"][0]["new"] == "customers.customer_unique_id"

    runs = api.get("/api/runs").json()["runs"]
    assert {item["id"] for item in runs} == {run_id, child["id"]}


def test_api_validation_and_error_envelopes(api):
    response = api.post("/api/runs", json={"task": "   ", "mode": "scripted", "dataset": "olist"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_task"

    response = api.post(
        "/api/runs", json={"task": "Do something not prepared.", "mode": "scripted", "dataset": "olist"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "unsupported_task"

    response = api.post(
        "/api/runs", json={"task": "What is our customer repeat purchase rate?", "mode": "live"}
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "live_unavailable"

    response = api.get("/api/runs/run_missing")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "run_not_found"

    response = api.get("/api/not-a-route")
    assert response.status_code == 404


def test_api_approval_gate_blocks_continue(api):
    view = _create(api, task="Delete canceled test orders from the sandbox.", dataset="sandbox")
    run_id = view["id"]
    for _ in range(3):
        response = api.post(f"/api/runs/{run_id}/step-over", json={})
        assert response.status_code == 200, response.text
    current = api.get(f"/api/runs/{run_id}").json()
    assert current["status"] == "awaiting_approval"
    assert current["approval"]["facts"][2]["label"] == "Affected rows"

    response = api.post(f"/api/runs/{run_id}/continue", json={})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "approval_pending"

    response = api.post(f"/api/runs/{run_id}/approval/approve", json={})
    assert response.status_code == 200
    assert response.json()["approval"] is None

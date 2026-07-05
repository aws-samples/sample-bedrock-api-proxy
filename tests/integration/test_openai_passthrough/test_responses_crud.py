"""Integration tests for the Responses CRUD endpoints — pure passthrough."""
import httpx


def test_get_response_forwards_and_returns_body(client, respx_mock, mock_usage_tracker):
    body = {"id": "r-1", "model": "x", "status": "completed"}
    respx_mock.get("/responses/r-1").mock(return_value=httpx.Response(200, json=body))

    r = client.get("/openai/v1/responses/r-1", headers={"Authorization": "Bearer sk-test"})
    assert r.status_code == 200
    assert r.json() == body
    # No usage logged for retrieval
    assert not mock_usage_tracker.record_usage_nowait.called


def test_delete_response_forwards(client, respx_mock):
    respx_mock.delete("/responses/r-1").mock(
        return_value=httpx.Response(200, json={"id": "r-1", "deleted": True})
    )
    r = client.delete("/openai/v1/responses/r-1", headers={"Authorization": "Bearer sk-test"})
    assert r.status_code == 200
    assert r.json() == {"id": "r-1", "deleted": True}


def test_cancel_response_forwards(client, respx_mock):
    respx_mock.post("/responses/r-1/cancel").mock(
        return_value=httpx.Response(200, json={"id": "r-1", "status": "cancelled"})
    )
    r = client.post("/openai/v1/responses/r-1/cancel", headers={"Authorization": "Bearer sk-test"})
    assert r.status_code == 200
    assert r.json()["status"] == "cancelled"


def test_list_input_items_forwards(client, respx_mock):
    body = {"data": [{"id": "msg-1", "role": "user"}], "object": "list"}
    respx_mock.get("/responses/r-1/input_items").mock(return_value=httpx.Response(200, json=body))
    r = client.get(
        "/openai/v1/responses/r-1/input_items",
        headers={"Authorization": "Bearer sk-test"},
    )
    assert r.status_code == 200
    assert r.json() == body


def test_get_response_404_returned_verbatim(client, respx_mock):
    respx_mock.get("/responses/missing").mock(
        return_value=httpx.Response(404, json={"error": {"message": "not found"}})
    )
    r = client.get("/openai/v1/responses/missing", headers={"Authorization": "Bearer sk-test"})
    assert r.status_code == 404
    assert r.json()["error"]["message"] == "not found"

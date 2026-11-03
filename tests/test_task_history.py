"""Phase 4 Task Center API and database queries (isolated by tests/conftest.py)."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from database import connection, repository
from database.history_repository import query_task_summaries
from task_history_api import router


@pytest.fixture
def client():
    connection.init_db()  # tests/conftest.py points DB_FILE at a disposable tmp path
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def make_task(*, name="<img src=x onerror=alert(1)>", done=True):
    task = repository.create_task("chat", "提问 <script>window.bad=1</script>", title=name)
    tid = task["task_id"]
    if done:
        repository.update_task_status(tid, "running")
        repository.save_message(tid, "user", "用户提问")
        repository.save_message(tid, "assistant", "模型回答", provider="chatgpt", transport="web")
        repository.save_provider_call(tid, provider="chatgpt", transport="web", action="new", status="done", duration_ms=9, conversation_url="https://chatgpt.com/c/123")
        repository.finish_task(tid, result_summary="完成")
    return tid


def test_empty_history(client):
    r = client.get("/api/tasks")
    assert r.status_code == 200
    assert r.json() == {"items": [], "total": 0, "limit": 20, "offset": 0}


def test_list_is_paginated_and_does_not_expose_prompts(client):
    for i in range(4):
        make_task(name=f"task-{i}")
    r = client.get("/api/tasks?limit=2&offset=1")
    assert r.status_code == 200
    data = r.json()
    assert len(data["items"]) == 2
    assert data["total"] == 4
    assert data["offset"] == 1
    assert "original_input" not in data["items"][0]
    assert "metadata_json" not in data["items"][0]
    assert data["items"][0]["title"] == "task-2"


def test_status_type_combined_filters(client):
    make_task(name="complete")
    waiting = make_task(name="not started", done=False)
    repository.create_task("auto", "placeholder")
    r = client.get("/api/tasks?status=queued&task_type=chat")
    assert r.status_code == 200
    assert r.json()["total"] == 1
    assert r.json()["items"][0]["task_id"] == waiting


def test_task_detail_returns_messages_events_calls(client):
    tid = make_task()
    r = client.get("/api/tasks/" + tid)
    assert r.status_code == 200
    data = r.json()
    assert data["task"]["original_input"].startswith("提问")
    assert [x["role"] for x in data["messages"]] == ["user", "assistant"]
    assert any(x["event_type"] == "task_created" for x in data["events"])
    assert data["provider_calls"][0]["provider"] == "chatgpt"


def test_not_found_and_invalid_filters(client):
    assert client.get("/api/tasks/unknown-id").status_code == 404
    assert client.get("/api/tasks?status=invalid").status_code == 422
    assert client.get("/api/tasks?task_type=chat%27%20OR%201%3D1").status_code == 422
    assert client.get("/api/tasks?limit=101").status_code == 422
    assert client.get("/api/tasks?offset=-1").status_code == 422
    assert client.get("/api/tasks/%3Cscript%3E").status_code == 422


def test_repository_rejects_invalid_pagination(client):
    for limit, offset in [(0, 0), (101, 0), (2, -1)]:
        with pytest.raises(ValueError):
            query_task_summaries(limit=limit, offset=offset)


def test_only_get_methods(client):
    assert client.post("/api/tasks", json={}).status_code == 405
    assert client.delete("/api/tasks/any").status_code == 405

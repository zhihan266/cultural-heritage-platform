"""Explicit HTTP business smoke test; defaults to read-only liveness/OpenAPI checks.

--create-demo creates two NEW accounts and one task, leaving auditable records.
One of two required answers is submitted, so this never queues billable aggregation.
"""

import argparse
import json
import secrets
from urllib.parse import urlsplit
from uuid import uuid4


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--create-demo", action="store_true")
    args = parser.parse_args()
    if urlsplit(args.api_url).hostname not in {"localhost", "127.0.0.1", "::1"}:
        parser.error("This smoke test only accepts a loopback API")
    import httpx

    with httpx.Client(base_url=args.api_url, timeout=20, trust_env=False) as http:
        response = http.get("/health")
        response.raise_for_status()
        assert response.json()["status"] == "ok"
        schema_response = http.get("/openapi.json")
        schema_response.raise_for_status()
        paths = schema_response.json()["paths"]
        assert all("get" in paths[path] for path in ["/rag-tasks", "/rag-tasks/mine/published", "/rag-tasks/mine/accepted"])
        assert not any(path.startswith("/rag-tasks/legacy") for path in paths)
        if not args.create_demo:
            print(json.dumps({"status": "passed", "scope": "read-only liveness/OpenAPI"}))
            return
        headers = []
        for _ in range(2):
            username, password = "judge-demo-" + uuid4().hex[:12], secrets.token_urlsafe(24)
            registered = http.post("/users/register", json={"username": username, "password": password})
            registered.raise_for_status()
            logged_in = http.post("/users/login", json={"username": username, "password": password})
            logged_in.raise_for_status()
            headers.append({"Authorization": "Bearer " + logged_in.json()["access_token"]})
        published = http.post("/rag-tasks", headers=headers[0], json={
            "title": "评委最小流程验证", "question": "请整理天坛的用途、事实来源和不确定点。", "worker_count": 2,
        })
        published.raise_for_status()
        task_id = published.json()["task_id"]
        hall = http.get("/rag-tasks", headers=headers[1])
        hall.raise_for_status()
        assert any(item["task_id"] == task_id for item in hall.json()["items"])
        accepted = http.post(f"/rag-tasks/{task_id}/accept", headers=headers[1])
        accepted.raise_for_status()
        submitted = http.post(f"/rag-tasks/{task_id}/answers", headers=headers[1], json={"content": "天坛用于祭天和祈谷。来源需进一步核实。"})
        submitted.raise_for_status()
        assert submitted.json()["status"] == "collecting" and submitted.json()["valid_worker_count"] == 1
        mine = http.get("/rag-tasks/mine/accepted", headers=headers[1])
        mine.raise_for_status()
        assert mine.json()["items"][0]["my_participation"]["status"] == "submitted"
    print(json.dumps({"status": "passed", "task_id": task_id, "scope": "real HTTP/JWT/crowd collection only; no model call"}, ensure_ascii=False))


if __name__ == "__main__":
    main()

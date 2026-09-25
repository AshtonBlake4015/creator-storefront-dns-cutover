import json
import os
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from dns_cutover import app


CHANGE = {
    "domain": "creator.example", "name": "shop", "previous_target": "old.example.net",
    "next_target": "new.example.net", "ttl": 60, "change_id": "launch-42",
}


def test_prepare_switch_and_rollback_use_the_same_zone_and_distinct_retry_keys():
    writes = []

    def respond(request):
        assert request.headers["Authorization"] == "Bearer test-key"
        if request.method == "GET":
            assert request.url.params["domain"] == CHANGE["domain"]
            return httpx.Response(200, json={"ok": True, "data": {"zone_id": "zone-42"}})
        writes.append((request.headers["Idempotency-Key"], request.content))
        return httpx.Response(200, json={"ok": True, "data": {}})

    transport_client = httpx.Client(transport=httpx.MockTransport(respond))
    with patch.dict(os.environ, {"INFRAI_API_KEY": "test-key"}), patch(
        "dns_cutover.httpx.Client", return_value=transport_client
    ):
        client = TestClient(app)
        results = [client.post(f"/cutover/{stage}", json=CHANGE) for stage in
                   ("prepare", "switch", "rollback")]

    assert [result.status_code for result in results] == [200, 200, 200]
    assert [result.json()["target"] for result in results] == [
        "old.example.net", "new.example.net", "old.example.net"
    ]
    assert [json.loads(payload)["content"] for _, payload in writes] == [
        "old.example.net", "new.example.net", "old.example.net"
    ]
    assert [key for key, _ in writes] == [
        "launch-42:prepare", "launch-42:switch", "launch-42:rollback"
    ]
    assert all(json.loads(payload)["zone_id"] == "zone-42" for _, payload in writes)


def test_api_rejection_keeps_its_client_status():
    def respond(request):
        return httpx.Response(400, json={"ok": False, "data": None,
                                          "error": {"code": "INVALID_ARGUMENT"}, "metadata": {}})

    transport_client = httpx.Client(transport=httpx.MockTransport(respond))
    with patch.dict(os.environ, {"INFRAI_API_KEY": "test-key"}), patch(
        "dns_cutover.httpx.Client", return_value=transport_client
    ):
        result = TestClient(app).post("/cutover/prepare", json=CHANGE)
    assert result.status_code == 400
    assert result.json()["detail"]["code"] == "INVALID_ARGUMENT"

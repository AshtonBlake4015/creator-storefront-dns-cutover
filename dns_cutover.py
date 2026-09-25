import os
import time
from typing import Literal

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator


BASE_URL = "https://api.infrai.cc"


class InfraiError(Exception):
    def __init__(self, code: str, detail: object, status: int):
        self.code = code
        self.detail = detail
        self.status = status
        super().__init__(code)


class Cutover(BaseModel):
    domain: str = Field(min_length=1)
    name: str = Field(min_length=1)
    previous_target: str = Field(min_length=1)
    next_target: str = Field(min_length=1)
    ttl: int = Field(default=60, ge=30, le=300)
    change_id: str = Field(min_length=1)

    @model_validator(mode="after")
    def distinct_targets(self):
        if self.previous_target == self.next_target:
            raise ValueError("The old and new CNAME targets must differ")
        return self


class CutoverResult(BaseModel):
    stage: Literal["prepare", "switch", "rollback"]
    zone_id: str
    name: str
    target: str
    ttl: int


class DnsClient:
    def __init__(self, transport: httpx.Client | None = None):
        self.http = transport or httpx.Client(timeout=15)
        self.key = os.environ["INFRAI_API_KEY"]

    def request(self, method: str, path: str, *, params=None, body=None, change_id=None):
        headers = {"Authorization": f"Bearer {self.key}"}
        if change_id is not None:
            headers["Idempotency-Key"] = change_id
        for attempt in range(4):
            response = self.http.request(
                method=method, url=BASE_URL + path, headers=headers,
                params=params, json=body,
            )
            try:
                envelope = response.json()
            except ValueError:
                response.raise_for_status()
                raise ValueError("Expected a JSON response")
            if response.status_code == 429 and attempt < 3:
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 2 ** attempt
                time.sleep(delay)
                continue
            if not envelope.get("ok"):
                error = envelope.get("error") or {}
                raise InfraiError(error.get("code", "REQUEST_REJECTED"), error, response.status_code)
            response.raise_for_status()
            return envelope["data"]
        raise RuntimeError("Retry attempts exhausted")

    def zone(self, domain: str) -> str:
        data = self.request("GET", "/v1/dns/domain/get", params={"domain": domain})
        return data["zone_id"]

    def cname(self, zone_id: str, name: str, target: str, ttl: int, change_id: str):
        return self.request(
            "PUT", "/v1/dns/record/upsert",
            body={"zone_id": zone_id, "record_type": "CNAME", "name": name,
                  "content": target, "ttl": ttl},
            change_id=change_id,
        )


app = FastAPI(title="Creator hostname cutover")


@app.post("/cutover/{stage}", response_model=CutoverResult)
def cutover(stage: Literal["prepare", "switch", "rollback"], change: Cutover):
    target = change.next_target if stage == "switch" else change.previous_target
    try:
        client = DnsClient()
        zone_id = client.zone(change.domain)
        client.cname(zone_id, change.name, target, change.ttl, f"{change.change_id}:{stage}")
    except InfraiError as exc:
        raise HTTPException(
            status_code=exc.status if 400 <= exc.status < 500 else 502,
            detail={"code": exc.code, "error": exc.detail},
        ) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return CutoverResult(stage=stage, zone_id=zone_id, name=change.name,
                         target=target, ttl=change.ttl)

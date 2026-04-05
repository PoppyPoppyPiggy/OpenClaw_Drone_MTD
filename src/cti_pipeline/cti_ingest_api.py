#!/usr/bin/env python3
"""
cti_ingest_api.py — CTI Ingest HTTP API

Project  : MIRAGE-UAS
Module   : CTI Pipeline / Ingest API
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[Inputs]
    - POST /ingest/event  : MavlinkCaptureEvent JSON (cti-interceptor에서 전송)
    - POST /ingest/batch  : list[MavlinkCaptureEvent] JSON

[Outputs]
    - asyncio.Queue[ParsedAttackEvent] → STIXConverter → Dataset
    - GET /status  : SystemStatus (논문 실험 모니터링)
    - GET /metrics : MetricsSummary (실시간 지표)

[의존성]
    - fastapi >= 0.115
    - uvicorn[standard] >= 0.30

[실행]
    uvicorn src.cti_pipeline.cti_ingest_api:app --host 0.0.0.0 --port 8765
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from shared.logger import get_logger
from shared.models import (
    AttackerLevel,
    DroneProtocol,
    MavlinkCaptureEvent,
    ParsedAttackEvent,
)
from cti_pipeline.attack_event_parser import AttackEventParser
from cti_pipeline.stix_converter import STIXConverter

logger = get_logger(__name__)

# ── 전역 상태 ──────────────────────────────────────────────────────────────────
# FastAPI lifespan에서 초기화
_parser          : Optional[AttackEventParser] = None
_stix_converter  : Optional[STIXConverter]     = None
_parsed_event_q  : asyncio.Queue               = asyncio.Queue()
_stats           : dict = {
    "total_ingested"   : 0,
    "total_attack"     : 0,
    "total_benign"     : 0,
    "started_at"       : time.time(),
    "last_event_at"    : 0.0,
}


# ── Lifespan ───────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    global _parser, _stix_converter
    _parser         = AttackEventParser()
    _stix_converter = STIXConverter()
    logger.info("cti_ingest_api started", port=8765)
    yield
    logger.info("cti_ingest_api shutdown")


# ── FastAPI 앱 ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="MIRAGE-UAS CTI Ingest API",
    version="0.1.0",
    description="Ingest honeydrone attack events, convert to STIX 2.1",
    lifespan=lifespan,
)


# ── Request / Response 모델 ───────────────────────────────────────────────────

class CaptureEventRequest(BaseModel):
    """MavlinkCaptureEvent JSON 수신 모델."""
    event_id     : str          = Field(default="")
    timestamp_ns : int          = Field(default=0)
    drone_id     : str
    src_ip       : str
    src_port     : int          = Field(default=0)
    protocol     : str          = Field(default="mavlink")
    msg_type     : str          = Field(default="")
    msg_id       : int          = Field(default=-1)
    sysid        : int          = Field(default=0)
    compid       : int          = Field(default=0)
    payload_hex  : str          = Field(default="")
    http_method  : str          = Field(default="")
    http_path    : str          = Field(default="")
    is_anomalous : bool         = Field(default=False)
    session_id   : str          = Field(default="")

    def to_model(self) -> MavlinkCaptureEvent:
        return MavlinkCaptureEvent(
            event_id=self.event_id or "",
            timestamp_ns=self.timestamp_ns or 0,
            drone_id=self.drone_id,
            src_ip=self.src_ip,
            src_port=self.src_port,
            protocol=DroneProtocol(self.protocol),
            msg_type=self.msg_type,
            msg_id=self.msg_id,
            sysid=self.sysid,
            compid=self.compid,
            payload_hex=self.payload_hex,
            http_method=self.http_method,
            http_path=self.http_path,
            is_anomalous=self.is_anomalous,
            session_id=self.session_id,
        )


class IngestResponse(BaseModel):
    event_id   : str
    bundle_id  : str
    ttp_count  : int
    level      : str


class BatchIngestResponse(BaseModel):
    count      : int
    bundle_id  : str
    ttp_count  : int


class SystemStatus(BaseModel):
    status             : str
    total_ingested     : int
    total_attack       : int
    total_benign       : int
    uptime_sec         : float
    last_event_sec_ago : float
    queue_depth        : int


class MetricsSummary(BaseModel):
    ingestion_rate_per_min : float
    attack_ratio           : float
    queue_depth            : int
    uptime_sec             : float


# ── 엔드포인트 ────────────────────────────────────────────────────────────────

@app.post(
    "/ingest/event",
    response_model=IngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="단일 캡처 이벤트 수신 및 STIX 변환",
)
async def ingest_event(req: CaptureEventRequest) -> IngestResponse:
    """
    [ROLE] MavlinkCaptureEvent 단일 수신 → 파싱 → STIX 번들 생성 → 큐 적재.

    [DATA FLOW]
        CaptureEventRequest
        ──▶ MavlinkCaptureEvent
        ──▶ AttackEventParser.parse() ──▶ ParsedAttackEvent
        ──▶ STIXConverter.convert()   ──▶ stix2.Bundle
        ──▶ _parsed_event_q           (Dataset Builder 소비)
    """
    if _parser is None or _stix_converter is None:
        raise HTTPException(status_code=503, detail="서버 초기화 중")

    event = req.to_model()
    try:
        parsed = _parser.parse(event)
    except Exception as e:
        logger.error("parse_failed", event_id=event.event_id[:8], error=str(e))
        raise HTTPException(status_code=422, detail=f"파싱 실패: {e}")

    bundle = _stix_converter.convert(parsed)
    parsed.stix_bundle_id = bundle.id
    await _parsed_event_q.put(parsed)

    _stats["total_ingested"] += 1
    _stats["total_attack"] += 1
    _stats["last_event_at"] = time.time()

    return IngestResponse(
        event_id=event.event_id,
        bundle_id=bundle.id,
        ttp_count=len(parsed.ttp_ids),
        level=parsed.attacker_level.name,
    )


@app.post(
    "/ingest/batch",
    response_model=BatchIngestResponse,
    status_code=status.HTTP_201_CREATED,
    summary="이벤트 배치 수신 및 단일 STIX 번들 변환",
)
async def ingest_batch(reqs: list[CaptureEventRequest]) -> BatchIngestResponse:
    """
    [ROLE] 복수 이벤트 일괄 수신 → 배치 파싱 → 단일 STIX Bundle 생성.
           Dataset packager의 에피소드 단위 수집에 사용.

    [DATA FLOW]
        list[CaptureEventRequest]
        ──▶ list[MavlinkCaptureEvent]
        ──▶ [parser.parse(e) for e]
        ──▶ stix_converter.batch_convert()
        ──▶ _parsed_event_q × N
    """
    if _parser is None or _stix_converter is None:
        raise HTTPException(status_code=503, detail="서버 초기화 중")
    if not reqs:
        raise HTTPException(status_code=400, detail="빈 배치")

    events  = [r.to_model() for r in reqs]
    parsed_list = []
    for ev in events:
        try:
            parsed_list.append(_parser.parse(ev))
        except Exception as e:
            logger.warning("batch_parse_skip", event_id=ev.event_id[:8], error=str(e))

    if not parsed_list:
        raise HTTPException(status_code=422, detail="모든 이벤트 파싱 실패")

    bundle = _stix_converter.batch_convert(parsed_list)
    total_ttp = sum(len(p.ttp_ids) for p in parsed_list)

    for parsed in parsed_list:
        parsed.stix_bundle_id = bundle.id
        await _parsed_event_q.put(parsed)

    _stats["total_ingested"] += len(parsed_list)
    _stats["total_attack"]   += len(parsed_list)
    _stats["last_event_at"]   = time.time()

    return BatchIngestResponse(
        count=len(parsed_list),
        bundle_id=bundle.id,
        ttp_count=total_ttp,
    )


@app.get("/status", response_model=SystemStatus, summary="파이프라인 상태 조회")
async def get_status() -> SystemStatus:
    """[ROLE] 현재 파이프라인 운영 상태 반환. 논문 실험 모니터링용."""
    now     = time.time()
    uptime  = now - _stats["started_at"]
    last_ev = now - _stats["last_event_at"] if _stats["last_event_at"] else 0.0
    return SystemStatus(
        status="running" if _parser is not None else "initializing",
        total_ingested=_stats["total_ingested"],
        total_attack=_stats["total_attack"],
        total_benign=_stats["total_benign"],
        uptime_sec=round(uptime, 1),
        last_event_sec_ago=round(last_ev, 1),
        queue_depth=_parsed_event_q.qsize(),
    )


@app.get("/metrics", response_model=MetricsSummary, summary="실시간 지표")
async def get_metrics() -> MetricsSummary:
    """[ROLE] 수집률, 공격 비율 등 실시간 지표 반환."""
    uptime = time.time() - _stats["started_at"]
    rate   = (_stats["total_ingested"] / uptime * 60) if uptime > 0 else 0.0
    total  = _stats["total_ingested"]
    ratio  = _stats["total_attack"] / total if total > 0 else 0.0
    return MetricsSummary(
        ingestion_rate_per_min=round(rate, 2),
        attack_ratio=round(ratio, 4),
        queue_depth=_parsed_event_q.qsize(),
        uptime_sec=round(uptime, 1),
    )


@app.delete("/reset", summary="에피소드 초기화 (실험 재시작 시)")
async def reset() -> dict:
    """
    [ROLE] 통계 및 큐를 초기화. 논문 실험의 에피소드 경계 표시.

    [DATA FLOW]
        DELETE /reset ──▶ _stats 초기화 ──▶ _parsed_event_q 비우기
    """
    while not _parsed_event_q.empty():
        _parsed_event_q.get_nowait()
    _stats.update({
        "total_ingested": 0,
        "total_attack"  : 0,
        "total_benign"  : 0,
        "started_at"    : time.time(),
        "last_event_at" : 0.0,
    })
    logger.info("cti_pipeline_reset")
    return {"ok": True, "reset_at": time.time()}


def get_parsed_event_queue() -> asyncio.Queue:
    """[ROLE] Dataset Builder가 ParsedAttackEvent를 소비하는 큐 반환."""
    return _parsed_event_q

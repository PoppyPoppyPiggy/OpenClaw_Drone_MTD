#!/usr/bin/env python3
"""
positive_collector.py — 공격(양성) 샘플 수집기

Project  : MIRAGE-UAS
Module   : Dataset / Positive Collector
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[Inputs]
    - asyncio.Queue[ParsedAttackEvent] (CTI Ingest API → cti_ingest_api.get_parsed_event_queue())

[Outputs]
    - list[DatasetEntry] (label=1, source="honeydrone")
    - results/dataset/positive_*.jsonl (스트리밍 저장)

[설계]
    - CTI 파이프라인이 생성한 ParsedAttackEvent를 소비
    - label=1 (attack), confidence = ParsedAttackEvent.confidence
    - STIX bundle ID 포함 → 원본 CTI와 1:1 연결
    - 중복 event_id 필터링 (동일 이벤트 중복 수집 방지)

[논문 기여]
    DVD-CTI-Dataset-v1의 양성(positive) 파티션 생성
    REF: MIRAGE-UAS §6 Dataset Construction
"""

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Optional

import aiofiles

from shared.constants import RESULTS_DIR
from shared.logger import get_logger
from shared.models import DatasetEntry, DroneProtocol, ParsedAttackEvent

logger = get_logger(__name__)

_POSITIVE_DIR = Path(RESULTS_DIR) / "dataset" / "positive"


class PositiveCollector:
    """
    [ROLE] CTI Ingest API의 ParsedAttackEvent 큐를 소비하여
           label=1 DatasetEntry를 생성하고 파일로 스트리밍 저장.

    [DATA FLOW]
        asyncio.Queue[ParsedAttackEvent] (cti_ingest_api)
        ──▶ PositiveCollector._consume_loop()
        ──▶ _to_dataset_entry(event)    ──▶ DatasetEntry (label=1)
        ──▶ _write_entry(entry)         ──▶ JSONL 파일 append
        ──▶ collected: list[DatasetEntry] (메모리 버퍼)
    """

    def __init__(self, event_queue: asyncio.Queue) -> None:
        self._event_q   = event_queue
        self._collected : list[DatasetEntry] = []
        self._seen_ids  : set[str]           = set()
        self._task      : Optional[asyncio.Task] = None
        self._session_id = str(uuid.uuid4())[:8]

    async def start(self) -> None:
        """
        [ROLE] 소비 루프 백그라운드 태스크 시작 + 출력 디렉토리 생성.

        [DATA FLOW]
            start() ──▶ asyncio.create_task(_consume_loop())
        """
        _POSITIVE_DIR.mkdir(parents=True, exist_ok=True)
        self._task = asyncio.create_task(
            self._consume_loop(), name="positive_collector"
        )
        logger.info(
            "positive_collector started",
            session=self._session_id,
            output_dir=str(_POSITIVE_DIR),
        )

    async def stop(self) -> None:
        """[ROLE] 소비 루프 종료 + 최종 수집 통계 로그."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info(
            "positive_collector stopped",
            session=self._session_id,
            total_collected=len(self._collected),
        )

    def get_entries(self) -> list[DatasetEntry]:
        """
        [ROLE] 현재까지 수집된 DatasetEntry 목록 반환 (메모리 버퍼).
               DatasetPackager가 소비.

        [DATA FLOW]
            ──▶ list[DatasetEntry] (snapshot, label=1 only)
        """
        return list(self._collected)

    def stats(self) -> dict:
        """[ROLE] 수집 통계 반환 (논문 Table IV 입력)."""
        levels = {}
        ttps   = set()
        for e in self._collected:
            lv = e.attacker_level.name if e.attacker_level else "unknown"
            levels[lv] = levels.get(lv, 0) + 1
            ttps.update(e.ttp_ids)
        return {
            "total"          : len(self._collected),
            "label"          : 1,
            "by_level"       : levels,
            "unique_ttps"    : sorted(ttps),
            "unique_ttp_count": len(ttps),
        }

    # ── 내부 루프 ─────────────────────────────────────────────────────────────

    async def _consume_loop(self) -> None:
        """
        [ROLE] ParsedAttackEvent 큐를 지속 소비하며 DatasetEntry 생성 및 저장.

        [DATA FLOW]
            asyncio.Queue.get() ──▶ ParsedAttackEvent
            ──▶ _to_dataset_entry() ──▶ DatasetEntry
            ──▶ _write_entry()      ──▶ JSONL append
            ──▶ self._collected.append()
        """
        while True:
            try:
                parsed: ParsedAttackEvent = await self._event_q.get()
                # 중복 제거
                eid = parsed.raw_event.event_id
                if eid in self._seen_ids:
                    continue
                self._seen_ids.add(eid)

                entry = self._to_dataset_entry(parsed)
                self._collected.append(entry)
                await self._write_entry(entry)

                logger.debug(
                    "positive_sample_collected",
                    event_id=eid[:8],
                    level=parsed.attacker_level.name,
                    ttps=parsed.ttp_ids,
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("positive_collector_error", error=str(e))

    def _to_dataset_entry(self, parsed: ParsedAttackEvent) -> DatasetEntry:
        """
        [ROLE] ParsedAttackEvent → DatasetEntry (label=1) 변환.

        [DATA FLOW]
            ParsedAttackEvent ──▶ DatasetEntry(label=1, source="honeydrone")
        """
        raw = parsed.raw_event
        return DatasetEntry(
            timestamp_ns    = raw.timestamp_ns,
            drone_id        = raw.drone_id,
            src_ip          = raw.src_ip,
            protocol        = raw.protocol,
            msg_type        = raw.msg_type,
            payload_hex     = raw.payload_hex[:256],   # 256 hex chars = 128 bytes
            attacker_level  = parsed.attacker_level,
            ttp_ids         = parsed.ttp_ids,
            stix_bundle_id  = parsed.stix_bundle_id,
            label           = 1,
            confidence      = parsed.confidence,
            source          = "honeydrone",
        )

    async def _write_entry(self, entry: DatasetEntry) -> None:
        """
        [ROLE] DatasetEntry를 JSONL 포맷으로 파일에 append 저장.
               논문 재현성을 위한 스트리밍 저장 (실험 도중 장애 시 복구 가능).

        [DATA FLOW]
            DatasetEntry ──▶ JSON dict ──▶ aiofiles.open (append) ──▶ JSONL
        """
        path = _POSITIVE_DIR / f"positive_{self._session_id}.jsonl"
        record = {
            "entry_id"       : entry.entry_id,
            "timestamp_ns"   : entry.timestamp_ns,
            "drone_id"       : entry.drone_id,
            "src_ip"         : entry.src_ip,
            "protocol"       : entry.protocol.value,
            "msg_type"       : entry.msg_type,
            "payload_hex"    : entry.payload_hex,
            "attacker_level" : entry.attacker_level.name if entry.attacker_level else None,
            "ttp_ids"        : entry.ttp_ids,
            "stix_bundle_id" : entry.stix_bundle_id,
            "label"          : entry.label,
            "confidence"     : entry.confidence,
            "source"         : entry.source,
        }
        try:
            async with aiofiles.open(path, "a", encoding="utf-8") as f:
                await f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.error("write_entry_failed", error=str(e))

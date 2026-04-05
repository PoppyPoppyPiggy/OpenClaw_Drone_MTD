#!/usr/bin/env python3
"""
deception_monitor.py — 실시간 기만 효과 모니터링

Project  : MIRAGE-UAS
Module   : Evaluation / Deception Monitor
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-06
Version  : 0.1.0

[Inputs]
    - CTI Ingest API (http://cti-api:8765)
      - GET /metrics  : 기만 지표 (5초 주기 폴링)
      - GET /status   : 시스템 상태 (10초 주기 폴링)

[Outputs]
    - results/metrics/deception_timeline.jsonl  (시계열 기록)
    - 실험 종료 시 최종 DeceptionScore 출력

[Dependencies]
    - aiohttp >= 3.9
    - asyncio (stdlib)

[설계 원칙]
    ① 모든 폴링 데이터를 JSONL 시계열로 보존 (재현성)
    ② sliding window (60초) 로 평균 confusion score 추적
    ③ 최종 DeceptionScore 산출은 deception_scorer.py에 위임 가능

[DATA FLOW]
    cti-api:8765/metrics ──▶ poll_metrics()
    cti-api:8765/status  ──▶ poll_status()
    ──▶ _compute_effectiveness()
    ──▶ deception_timeline.jsonl
"""

import asyncio
import json
import os
import time
from collections import deque
from pathlib import Path
from typing import Any

import aiohttp

from shared.logger import get_logger

logger = get_logger(__name__)

# ── 설정 ──────────────────────────────────────────────────────────────────────
_CTI_API_BASE      = os.environ.get("CTI_API_BASE", "http://cti-api:8765")
_METRICS_POLL_SEC  = 5.0
_STATUS_POLL_SEC   = 10.0
_SLIDING_WINDOW_SEC = 60.0
_RESULTS_DIR       = Path(os.environ.get("RESULTS_DIR", "results")) / "metrics"
_TIMELINE_FILE     = _RESULTS_DIR / "deception_timeline.jsonl"


class DeceptionMonitor:
    """
    [ROLE] CTI Ingest API를 실시간 폴링하여 기만 효과 시계열을 기록.
           실험 종료 시 최종 DeceptionScore를 산출·출력.

    [DATA FLOW]
        start() ──▶ _metrics_loop() + _status_loop() (병렬)
        ──▶ _append_record() ──▶ deception_timeline.jsonl
        stop() ──▶ _print_final_report()
    """

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._tasks: list[asyncio.Task] = []
        # 시계열 누적
        self._total_sessions: int = 0
        self._protected_sessions: int = 0
        self._total_connections: int = 0
        self._ghost_connections: int = 0
        self._breadcrumbs_planted: int = 0
        self._breadcrumbs_taken: int = 0
        # sliding window: (timestamp, confusion_score)
        self._confusion_window: deque[tuple[float, float]] = deque()

    async def start(self) -> None:
        """
        [ROLE] 모니터링 루프 시작.

        [DATA FLOW]
            start() ──▶ aiohttp.ClientSession 생성
            ──▶ _metrics_loop() / _status_loop() 태스크
        """
        _RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        self._session = aiohttp.ClientSession()
        self._tasks = [
            asyncio.create_task(self._metrics_loop(), name="monitor_metrics"),
            asyncio.create_task(self._status_loop(), name="monitor_status"),
        ]
        logger.info("deception_monitor started", api_base=_CTI_API_BASE)

    async def stop(self) -> None:
        """
        [ROLE] 모니터링 루프 종료 + 최종 보고서 출력.

        [DATA FLOW]
            stop() ──▶ task.cancel() ──▶ _print_final_report()
            ──▶ session.close()
        """
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._print_final_report()
        if self._session:
            await self._session.close()
        logger.info("deception_monitor stopped")

    async def _metrics_loop(self) -> None:
        """
        [ROLE] /metrics 엔드포인트 5초 주기 폴링.

        [DATA FLOW]
            GET /metrics ──▶ JSON 파싱 ──▶ 지표 갱신 ──▶ 타임라인 기록
        """
        while True:
            try:
                await asyncio.sleep(_METRICS_POLL_SEC)
                data = await self._fetch("/metrics")
                if data is None:
                    continue

                # 지표 갱신
                self._total_sessions = data.get("total_sessions", self._total_sessions)
                self._protected_sessions = data.get("protected_sessions", self._protected_sessions)
                self._total_connections = data.get("total_connections", self._total_connections)
                self._ghost_connections = data.get("ghost_connections", self._ghost_connections)
                self._breadcrumbs_planted = data.get("breadcrumbs_planted", self._breadcrumbs_planted)
                self._breadcrumbs_taken = data.get("breadcrumbs_taken", self._breadcrumbs_taken)

                confusion = data.get("avg_confusion_score", 0.0)
                now = time.time()
                self._confusion_window.append((now, confusion))
                # sliding window 정리
                cutoff = now - _SLIDING_WINDOW_SEC
                while self._confusion_window and self._confusion_window[0][0] < cutoff:
                    self._confusion_window.popleft()

                self._append_record(data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("metrics_loop error", error=str(e))

    async def _status_loop(self) -> None:
        """
        [ROLE] /status 엔드포인트 10초 주기 폴링.

        [DATA FLOW]
            GET /status ──▶ JSON 파싱 ──▶ 로그 기록
        """
        while True:
            try:
                await asyncio.sleep(_STATUS_POLL_SEC)
                data = await self._fetch("/status")
                if data is not None:
                    logger.debug("cti_status", data=data)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("status_loop error", error=str(e))

    async def _fetch(self, path: str) -> dict | None:
        """
        [ROLE] CTI API GET 요청.

        [DATA FLOW]
            path ──▶ GET _CTI_API_BASE+path ──▶ JSON dict
        """
        if self._session is None:
            return None
        url = f"{_CTI_API_BASE}{path}"
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    return await resp.json()
                logger.debug("fetch_non_200", url=url, status=resp.status)
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            logger.debug("fetch_error", url=url, error=str(e))
        return None

    def _append_record(self, raw_data: dict) -> None:
        """
        [ROLE] 타임라인 JSONL에 레코드 추가.

        [DATA FLOW]
            raw_data ──▶ 효과 지표 계산 ──▶ JSONL 파일 추가
        """
        effectiveness = self._protected_sessions / max(self._total_sessions, 1)
        ghost_hit_rate = self._ghost_connections / max(self._total_connections, 1)
        breadcrumb_follow_rate = self._breadcrumbs_taken / max(self._breadcrumbs_planted, 1)

        # sliding window 평균 confusion
        if self._confusion_window:
            avg_confusion = sum(v for _, v in self._confusion_window) / len(self._confusion_window)
        else:
            avg_confusion = 0.0

        record = {
            "timestamp": time.time(),
            "deception_effectiveness": round(effectiveness, 4),
            "avg_confusion_score": round(avg_confusion, 4),
            "ghost_service_hit_rate": round(ghost_hit_rate, 4),
            "breadcrumb_follow_rate": round(breadcrumb_follow_rate, 4),
            "total_sessions": self._total_sessions,
            "protected_sessions": self._protected_sessions,
            "total_connections": self._total_connections,
            "ghost_connections": self._ghost_connections,
            "breadcrumbs_planted": self._breadcrumbs_planted,
            "breadcrumbs_taken": self._breadcrumbs_taken,
            "raw": raw_data,
        }
        with open(_TIMELINE_FILE, "a") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _print_final_report(self) -> None:
        """
        [ROLE] 실험 종료 시 최종 기만 효과 보고서 출력.

        [DATA FLOW]
            누적 지표 ──▶ 최종 DeceptionScore 계산 ──▶ stdout
        """
        effectiveness = self._protected_sessions / max(self._total_sessions, 1)
        ghost_hit = self._ghost_connections / max(self._total_connections, 1)
        breadcrumb_rate = self._breadcrumbs_taken / max(self._breadcrumbs_planted, 1)

        if self._confusion_window:
            avg_confusion = sum(v for _, v in self._confusion_window) / len(self._confusion_window)
        else:
            avg_confusion = 0.0

        report = (
            "\n"
            "═══════════════════════════════════════════════\n"
            "  MIRAGE-UAS Deception Monitor — Final Report  \n"
            "═══════════════════════════════════════════════\n"
            f"  Deception Effectiveness : {effectiveness:.1%}\n"
            f"  Avg Confusion Score     : {avg_confusion:.3f}\n"
            f"  Ghost Service Hit Rate  : {ghost_hit:.1%}\n"
            f"  Breadcrumb Follow Rate  : {breadcrumb_rate:.1%}\n"
            f"  Total Sessions          : {self._total_sessions}\n"
            f"  Protected Sessions      : {self._protected_sessions}\n"
            "═══════════════════════════════════════════════\n"
        )
        print(report)
        logger.info(
            "final_report",
            deception_effectiveness=round(effectiveness, 4),
            avg_confusion=round(avg_confusion, 4),
            ghost_hit_rate=round(ghost_hit, 4),
            breadcrumb_follow_rate=round(breadcrumb_rate, 4),
        )


async def main() -> None:
    """
    [ROLE] 스탠드얼론 실행 진입점.

    [DATA FLOW]
        main() ──▶ DeceptionMonitor.start() ──▶ 무한 대기
        ──▶ SIGINT/SIGTERM ──▶ stop()
    """
    monitor = DeceptionMonitor()
    await monitor.start()
    try:
        # 무한 대기 (Docker 컨테이너 라이프사이클)
        while True:
            await asyncio.sleep(3600)
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        await monitor.stop()


if __name__ == "__main__":
    asyncio.run(main())

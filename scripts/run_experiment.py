#!/usr/bin/env python3
"""
run_experiment.py — MIRAGE-UAS 전체 실험 실행 진입점

Project  : MIRAGE-UAS
Module   : Scripts / Experiment Runner
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[역할]
    모든 컴포넌트를 오케스트레이션하여 논문 재현 가능한 실험 실행.
    Track A (MTD + Agentic Deception) + Track B (CTI + Dataset) 동시 운용.

[실행 모드]
    --mode full     : Docker 컨테이너 실제 기동 (WSL2 + Docker Desktop 필요)
    --mode dry-run  : Docker 없이 파이프라인 + 합성 데이터로 실험 (논문 시뮬레이션)
    --mode cti-only : Track B만 실행 (Dataset 수집 전용)

[사용법]
    # 전체 실험 (60초)
    python scripts/run_experiment.py --mode full --duration 60

    # 논문 시뮬레이션 (Docker 없이, 300초)
    python scripts/run_experiment.py --mode dry-run --duration 300

    # CTI 파이프라인만
    python scripts/run_experiment.py --mode cti-only --duration 120

[출력]
    results/
    ├── metrics/table_ii_engagement.json
    ├── metrics/table_iii_mtd_latency.json
    ├── metrics/table_iv_dataset.json
    ├── metrics/table_v_deception.json
    ├── metrics/summary.json
    └── dataset/DVD-CTI-Dataset-v1/
        ├── dataset.csv
        ├── metadata.yaml
        └── README.md
"""

import argparse
import asyncio
import os
import sys
import time
import uuid
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가 (패키지 임포트)
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from shared.logger import get_logger
from shared.models import (
    AttackerLevel, DroneProtocol, EngagementMetrics,
    HoneyDroneConfig, MavlinkCaptureEvent, ParsedAttackEvent,
)

logger = get_logger(__name__)

# ── 실험 기본 설정 (인프라 고정값) ────────────────────────────────────────────
_DEFAULT_DURATION_SEC    : int = 120
_NEGATIVE_SAMPLE_RATIO   : float = 1.5   # 양성 1개당 음성 1.5개
_TELEMETRY_INTERVAL_SEC  : float = 5.0   # 실험 진행 상황 출력 간격


# ════════════════════════════════════════════════════════════════════════════════
# MirageExperiment — 전체 실험 오케스트레이터
# ════════════════════════════════════════════════════════════════════════════════

class MirageExperiment:
    """
    [ROLE] MIRAGE-UAS 전체 실험의 생애주기 관리.
           모든 컴포넌트를 asyncio 기반으로 동시 실행하고
           실험 종료 시 메트릭 수집 및 데이터셋 패키징.

    [DATA FLOW]
        run()
        ├── _setup()        ─ 컴포넌트 초기화 + 큐 연결
        ├── _start_all()    ─ 모든 컴포넌트 비동기 시작
        ├── _monitor_loop() ─ 진행 상황 출력 (duration동안)
        ├── _stop_all()     ─ 정지 + 최종 이벤트 드레인
        └── _finalize()     ─ Dataset 패키징 + 메트릭 저장
    """

    def __init__(self, mode: str, duration_sec: int, exp_id: str) -> None:
        self._mode        = mode
        self._duration    = duration_sec
        self._exp_id      = exp_id

        # ── asyncio 큐 (컴포넌트 간 데이터 흐름) ─────────────────────────────
        # Track A: MTDTrigger 흐름
        self._mtd_trigger_q : asyncio.Queue = asyncio.Queue()
        # Track A→B: Decoy engine → CTI pipeline
        self._decoy_event_q : asyncio.Queue = asyncio.Queue()
        # Track B: 캡처 이벤트 → 파서
        self._capture_q     : asyncio.Queue = asyncio.Queue()

        # ── 수집된 데이터 버퍼 (종료 시 메트릭 집계용) ───────────────────────
        self._all_metrics    : list[EngagementMetrics] = []
        self._all_mtd_results: list = []

        # ── 컴포넌트 레퍼런스 ─────────────────────────────────────────────────
        self._drone_mgr     = None
        self._engines       : list = []
        self._interceptor   = None
        self._http_capture  = None
        self._parser        = None
        self._pos_collector = None
        self._neg_generator = None
        self._mtd_executor  = None
        self._metrics       = None
        self._tasks         : list[asyncio.Task] = []

    async def run(self) -> None:
        """
        [ROLE] 실험 전체 실행: setup → start → monitor → stop → finalize.

        [DATA FLOW]
            run()
            ──▶ _setup()
            ──▶ _start_all()
            ──▶ _monitor_loop() (duration초)
            ──▶ _stop_all()
            ──▶ _finalize()
        """
        logger.info(
            "experiment_started",
            exp_id=self._exp_id,
            mode=self._mode,
            duration_sec=self._duration,
        )
        print(f"\n{'═'*60}")
        print(f"  MIRAGE-UAS Experiment [{self._exp_id[:8]}]")
        print(f"  Mode: {self._mode} | Duration: {self._duration}s")
        print(f"{'═'*60}\n")

        try:
            await self._setup()
            await self._start_all()
            await self._monitor_loop()
        except KeyboardInterrupt:
            logger.info("experiment_interrupted_by_user")
        except Exception as e:
            logger.error("experiment_error", error=str(e))
            raise
        finally:
            await self._stop_all()
            await self._finalize()

    # ── 컴포넌트 초기화 ──────────────────────────────────────────────────────

    async def _setup(self) -> None:
        """
        [ROLE] 모든 컴포넌트 인스턴스 생성 + 큐 연결.
               실제 Docker 기동은 _start_all()에서 수행.

        [DATA FLOW]
            mode ──▶ 컴포넌트 선택적 초기화
            ──▶ 큐 참조 주입 (의존성 주입)
        """
        from cti_pipeline.attack_event_parser import AttackEventParser
        from cti_pipeline.stix_converter import STIXConverter
        from dataset.positive_collector import PositiveCollector
        from dataset.negative_generator import NegativeGenerator
        from evaluation.metrics_collector import MetricsCollector

        self._parser        = AttackEventParser()
        self._stix_conv     = STIXConverter()
        # PositiveCollector는 파서의 큐를 직접 소비
        self._parsed_q      = asyncio.Queue()
        self._pos_collector = PositiveCollector(self._parsed_q)
        self._neg_generator = NegativeGenerator()
        self._metrics       = MetricsCollector(self._exp_id)

        if self._mode == "full":
            from honey_drone.honey_drone_manager import HoneyDroneManager
            from honey_drone.agentic_decoy_engine import AgenticDecoyEngine
            from cti_pipeline.mavlink_interceptor import MavlinkInterceptor
            from cti_pipeline.http_rtsp_capture import HTTPRTSPCapture
            from mtd.mtd_executor import MTDExecutor

            configs = HoneyDroneManager.build_configs()
            self._drone_mgr = HoneyDroneManager()
            self._configs   = configs

            for config in configs:
                engine = AgenticDecoyEngine(
                    config, self._mtd_trigger_q, self._decoy_event_q
                )
                self._engines.append(engine)

            self._interceptor  = MavlinkInterceptor(configs, self._capture_q)
            self._http_capture = HTTPRTSPCapture(configs, self._capture_q)
            self._mtd_executor = MTDExecutor(self._drone_mgr, self._all_mtd_results)

        elif self._mode in ("dry-run", "cti-only"):
            # Docker 없이 합성 이벤트로 파이프라인 테스트
            self._configs = []
            logger.info("dry_run_mode: Docker 컨테이너 없이 실행")

        logger.info("experiment_setup_complete", mode=self._mode)

    async def _start_all(self) -> None:
        """
        [ROLE] 모든 컴포넌트 비동기 시작 + 배경 태스크 등록.

        [DATA FLOW]
            순서: DroneManager → Engines → Interceptors → Collectors → MTD Executor
        """
        # Track B: Dataset 수집 시작
        await self._pos_collector.start()

        if self._mode == "full":
            # DVD 컨테이너 기동
            for config in self._configs:
                instance = await self._drone_mgr.spawn(config)
                logger.info("drone_spawned", drone_id=config.drone_id)

            # Agentic Decoy Engine 시작
            for engine in self._engines:
                await engine.start()

            # 인터셉터 시작
            if self._interceptor:
                await self._interceptor.start()
            if self._http_capture:
                await self._http_capture.start()

            # MTD Executor 소비 루프
            if self._mtd_executor:
                self._tasks.append(
                    asyncio.create_task(
                        self._mtd_executor.run(self._mtd_trigger_q),
                        name="mtd_executor"
                    )
                )

        # Track B: 파이프라인 처리 태스크 (모든 모드 공통)
        self._tasks.append(
            asyncio.create_task(
                self._pipeline_loop(), name="pipeline_loop"
            )
        )

        # dry-run: 합성 이벤트 생성기 시작
        if self._mode in ("dry-run",):
            self._tasks.append(
                asyncio.create_task(
                    self._synthetic_event_generator(), name="synthetic_gen"
                )
            )

        logger.info("all_components_started", mode=self._mode)

    async def _monitor_loop(self) -> None:
        """
        [ROLE] 실험 duration 동안 주기적 진행 상황 출력.

        [DATA FLOW]
            asyncio.sleep(_TELEMETRY_INTERVAL_SEC)
            ──▶ 큐 깊이 + 수집 카운트 출력
        """
        start   = time.time()
        end_at  = start + self._duration

        while time.time() < end_at:
            elapsed   = time.time() - start
            remaining = end_at - time.time()
            pos_count = len(self._pos_collector.get_entries()) \
                        if self._pos_collector else 0

            print(
                f"  [{elapsed:6.1f}s / {self._duration}s] "
                f"capture_q={self._capture_q.qsize():4d}  "
                f"mtd_q={self._mtd_trigger_q.qsize():3d}  "
                f"positive_samples={pos_count:5d}  "
                f"remain={remaining:.0f}s"
            )
            await asyncio.sleep(min(_TELEMETRY_INTERVAL_SEC, remaining))

    async def _stop_all(self) -> None:
        """
        [ROLE] 모든 태스크와 컴포넌트를 순서대로 정지.

        [DATA FLOW]
            태스크 취소 → Engine 정지 → 인터셉터 정지 → 드론 종료
        """
        # 태스크 취소
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

        # 컴포넌트 정지 (역순)
        if self._mode == "full":
            for engine in self._engines:
                await engine.stop()
            if self._http_capture:
                await self._http_capture.stop()
            if self._interceptor:
                await self._interceptor.stop()
            if self._drone_mgr:
                for inst in self._drone_mgr.list_active():
                    await self._drone_mgr.teardown(inst.config.drone_id)

        if self._pos_collector:
            await self._pos_collector.stop()

        logger.info("all_components_stopped")

    async def _finalize(self) -> None:
        """
        [ROLE] 실험 종료 후 Dataset 패키징 + 메트릭 수집 + 검증 수행.

        [DATA FLOW]
            positive_entries + negative_entries
            ──▶ DatasetPackager.package()
            ──▶ DatasetValidator.validate()
            ──▶ MetricsCollector.* (Table II~V)
            ──▶ 결과 출력
        """
        from dataset.dataset_packager import DatasetPackager
        from dataset.dataset_validator import DatasetValidator

        print(f"\n{'─'*60}")
        print("  실험 종료 — 데이터셋 패키징 중...")

        positive = self._pos_collector.get_entries() if self._pos_collector else []
        n_neg    = max(1, int(len(positive) * _NEGATIVE_SAMPLE_RATIO))
        negative = await self._neg_generator.generate(n_neg, method="synthetic")

        packager  = DatasetPackager()
        validator = DatasetValidator()

        output_dir = await packager.package(positive, negative)
        all_entries = positive + negative
        report  = validator.validate(all_entries)
        validator.save_report(report)

        # 메트릭 수집
        if self._all_metrics:
            self._metrics.collect_engagement(self._all_metrics)
            self._metrics.collect_deception(self._all_metrics)
        if self._all_mtd_results:
            self._metrics.collect_mtd_results(self._all_mtd_results)
        self._metrics.collect_dataset_stats(all_entries)

        pos_col   = self._pos_collector
        summary   = self._metrics.generate_summary(
            honey_drone_count=len(self._configs),
            total_sessions=len(self._all_metrics),
            total_mtd_actions=len(self._all_mtd_results),
            deception_success=sum(
                1 for m in self._all_metrics if not m.real_drone_breached
            ) / max(len(self._all_metrics), 1),
            dataset_size=len(all_entries),
            unique_ttps=len({
                ttp for e in positive for ttp in e.ttp_ids
            }),
        )

        print(f"\n{'═'*60}")
        print(f"  ✅  Dataset: {output_dir}")
        print(f"  ✅  Positive samples : {len(positive)}")
        print(f"  ✅  Negative samples : {len(negative)}")
        print(f"  ✅  Validation       : {'PASS' if report.passed else 'FAIL'}")
        print(f"  ✅  Duration         : {summary.duration_sec:.1f}s")
        print(f"  ✅  MTD actions      : {len(self._all_mtd_results)}")
        print(f"{'═'*60}\n")

    # ── 배경 태스크 ──────────────────────────────────────────────────────────

    async def _pipeline_loop(self) -> None:
        """
        [ROLE] capture_q + decoy_event_q에서 이벤트를 소비하여
               AttackEventParser → STIXConverter → parsed_q 처리.

        [DATA FLOW]
            capture_q / decoy_event_q
            ──▶ AttackEventParser.parse()
            ──▶ STIXConverter.convert()
            ──▶ parsed_q (PositiveCollector 소비)
        """
        while True:
            try:
                # 두 큐에서 이벤트 수집 (우선순위 없음)
                event: MavlinkCaptureEvent = await _dequeue_any(
                    self._capture_q, self._decoy_event_q
                )
                parsed = self._parser.parse(event)
                bundle = self._stix_conv.convert(parsed)
                parsed.stix_bundle_id = bundle.id
                await self._parsed_q.put(parsed)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("pipeline_loop_error", error=str(e))

    async def _synthetic_event_generator(self) -> None:
        """
        [ROLE] dry-run 모드 전용 합성 공격 이벤트 생성기.
               실제 허니드론 없이 L0-L4 시뮬레이션 이벤트 생성.

        [DATA FLOW]
            AttackerLevel 분포 ──▶ MavlinkCaptureEvent 생성
            ──▶ capture_q (pipeline_loop 소비)
        """
        import random
        import struct
        from shared.constants import ATTACKER_PRIOR_L0, ATTACKER_PRIOR_L1

        _ATTACK_MSGS = [
            "HEARTBEAT", "COMMAND_LONG", "PARAM_REQUEST_LIST",
            "PARAM_SET", "MISSION_ITEM", "FILE_TRANSFER_PROTOCOL",
            "SET_POSITION_TARGET_LOCAL_NED", "LOG_REQUEST_LIST",
        ]

        count = 0
        while True:
            try:
                await asyncio.sleep(random.uniform(0.1, 0.5))
                level_weights = [
                    ATTACKER_PRIOR_L0, ATTACKER_PRIOR_L1,
                    0.25, 0.15, 0.05
                ]
                level_idx = random.choices(range(5), weights=level_weights, k=1)[0]
                level = list(AttackerLevel)[level_idx]

                # L3-L4는 WebSocket 이벤트 포함
                protocol = DroneProtocol.WEBSOCKET \
                    if (level.value >= 3 and random.random() < 0.3) \
                    else DroneProtocol.MAVLINK

                msg_type = "WS_MESSAGE" if protocol == DroneProtocol.WEBSOCKET \
                    else random.choice(_ATTACK_MSGS)

                # CVE exploit 시뮬레이션 (L4)
                payload = b"Origin: null" if level == AttackerLevel.L4_APT \
                    else bytes(random.randint(0, 255) for _ in range(8))

                event = MavlinkCaptureEvent(
                    drone_id=f"honey_{random.randint(1,3):02d}",
                    src_ip=f"192.168.{random.randint(1,254)}.{random.randint(1,254)}",
                    src_port=random.randint(10000, 65000),
                    protocol=protocol,
                    msg_type=msg_type,
                    payload_hex=payload.hex(),
                    session_id=f"sim_{count:04d}",
                )
                await self._capture_q.put(event)
                count += 1
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("synthetic_gen_error", error=str(e))


# ── 유틸리티 ─────────────────────────────────────────────────────────────────

async def _dequeue_any(*queues: asyncio.Queue) -> Any:
    """
    [ROLE] 여러 큐 중 하나에서 아이템을 반환 (먼저 도착한 것).
           asyncio.wait + FIRST_COMPLETED 패턴.

    [DATA FLOW]
        queues ──▶ pending get() tasks ──▶ first done ──▶ result
    """
    pending = {asyncio.ensure_future(q.get()) for q in queues}
    done, pending_rest = await asyncio.wait(
        pending, return_when=asyncio.FIRST_COMPLETED
    )
    for task in pending_rest:
        task.cancel()
    return next(iter(done)).result()


from typing import Any


# ── CLI 진입점 ────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MIRAGE-UAS Experiment Runner",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--mode", choices=["full", "dry-run", "cti-only"],
        default="dry-run",
        help="full=Docker 실제기동 / dry-run=합성데이터 / cti-only=CTI만",
    )
    parser.add_argument(
        "--duration", type=int, default=_DEFAULT_DURATION_SEC,
        help=f"실험 시간 (초, 기본값={_DEFAULT_DURATION_SEC})",
    )
    parser.add_argument(
        "--exp-id", type=str, default=None,
        help="실험 ID (미지정 시 자동 생성)",
    )
    return parser.parse_args()


async def _main() -> None:
    args   = parse_args()
    exp_id = args.exp_id or str(uuid.uuid4())[:8]

    experiment = MirageExperiment(
        mode=args.mode,
        duration_sec=args.duration,
        exp_id=exp_id,
    )
    await experiment.run()


if __name__ == "__main__":
    asyncio.run(_main())

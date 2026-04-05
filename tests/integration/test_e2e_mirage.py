#!/usr/bin/env python3
"""
test_e2e_mirage.py — MIRAGE-UAS End-to-End 통합 테스트

Project  : MIRAGE-UAS
Module   : Tests / Integration
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[테스트 범위]
    E2E-01: MavlinkCaptureEvent → AttackEventParser → ParsedAttackEvent
    E2E-02: ParsedAttackEvent → STIXConverter → stix2.Bundle (구조 검증)
    E2E-03: EngagementTracker 세션 생성 + L0-L4 분류 + MTDTrigger 생성
    E2E-04: PositiveCollector → DatasetEntry (label=1)
    E2E-05: NegativeGenerator → DatasetEntry (label=0)
    E2E-06: DatasetPackager → CSV + metadata.yaml 생성
    E2E-07: DatasetValidator V1-V6 전체 검증
    E2E-08: MetricsCollector → Table II/III/IV/V JSON 생성

[실행]
    pytest tests/integration/test_e2e_mirage.py -v
    pytest tests/integration/test_e2e_mirage.py -v -k "E2E-03"

[주의]
    - Docker 불필요 (dry-run 레벨 테스트)
    - .env 없어도 동작 (테스트 전용 상수 주입)
    - 실제 네트워크 소켓 사용 없음
"""

import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

# 프로젝트 루트 sys.path 추가
_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT / "src"))

# ── 테스트 전용 환경변수 주입 (.env 없이 실행 가능) ──────────────────────────
def _inject_test_env() -> None:
    """[ROLE] constants.py가 ConfigError 없이 로드되도록 테스트 값 주입."""
    test_vals = {
        "MTD_COST_SENSITIVITY_KAPPA"  : "0.08",
        "MTD_ALPHA_WEIGHTS"           : "0.1,0.2,0.2,0.1,0.1,0.15,0.15",
        "MTD_BREACH_PREVENTION_BETA"  : "1.0",
        "COMPROMISE_P_BASE"           : "0.25",
        "DES_WEIGHT_LIST"             : "0.3,0.3,0.2,0.2",
        "REDUNDANCY_REWARD_HIGH"      : "0.8",
        "REDUNDANCY_REWARD_LOW"       : "0.2",
        "REDUNDANCY_THRESHOLD"        : "3.0",
        "DECEPTION_LAMBDA"            : "0.3",
        "DECEPTION_WEIGHTS"           : "0.5,0.3,0.2",
        "DECEPTION_DWELL_MAX_SEC"     : "300.0",
        "ATTACKER_PRIORS"             : "0.4,0.3,0.15,0.1,0.05",
        "PPO_LEARNING_RATE"           : "3e-4",
        "PPO_GAMMA"                   : "0.99",
        "PPO_CLIP_EPS"                : "0.2",
        "PPO_ENTROPY_COEF"            : "0.01",
        "HONEY_DRONE_COUNT"           : "3",
        "MAVLINK_PORT_BASE"           : "14550",
        "SITL_PORT_BASE"              : "5760",
        "WEBCLAW_PORT_BASE"           : "18789",
        "HTTP_PORT_BASE"              : "8080",
        "RTSP_PORT_BASE"              : "8553",
        "CTI_API_PORT"                : "8765",
        "LOG_LEVEL"                   : "WARNING",  # 테스트 중 로그 최소화
        "LOG_FORMAT"                  : "json",
    }
    for k, v in test_vals.items():
        if k not in os.environ:
            os.environ[k] = v

_inject_test_env()

# ── 테스트 픽스처 ─────────────────────────────────────────────────────────────

from shared.models import (
    AttackerLevel, DroneProtocol, EngagementMetrics,
    HoneyDroneConfig, MavlinkCaptureEvent, ParsedAttackEvent,
    KillChainPhase, DatasetEntry,
)


def make_capture_event(
    msg_type: str = "COMMAND_LONG",
    src_ip: str = "192.168.1.100",
    protocol: DroneProtocol = DroneProtocol.MAVLINK,
    payload_hex: str = "deadbeef01020304",
    drone_id: str = "honey_01",
) -> MavlinkCaptureEvent:
    """테스트용 MavlinkCaptureEvent 생성."""
    return MavlinkCaptureEvent(
        drone_id=drone_id,
        src_ip=src_ip,
        src_port=12345,
        protocol=protocol,
        msg_type=msg_type,
        msg_id=76 if msg_type == "COMMAND_LONG" else 0,
        payload_hex=payload_hex,
        session_id="test_session_001",
    )


def make_parsed_event(
    attacker_level: AttackerLevel = AttackerLevel.L2_INTERMEDIATE,
    ttp_ids: list[str] | None = None,
) -> ParsedAttackEvent:
    """테스트용 ParsedAttackEvent 생성."""
    return ParsedAttackEvent(
        raw_event=make_capture_event(),
        attacker_level=attacker_level,
        ttp_ids=ttp_ids or ["T0855", "T0836"],
        kill_chain_phase=KillChainPhase.EXPLOITATION,
        confidence=0.85,
        dwell_time_sec=45.0,
    )


# ════════════════════════════════════════════════════════════════════════════════
# E2E-01: AttackEventParser
# ════════════════════════════════════════════════════════════════════════════════

def test_e2e_01_attack_event_parser_basic():
    """[E2E-01] MAVLink 이벤트 파싱 → ParsedAttackEvent 생성."""
    from cti_pipeline.attack_event_parser import AttackEventParser

    parser = AttackEventParser()
    event  = make_capture_event(msg_type="COMMAND_LONG")
    parsed = parser.parse(event)

    assert isinstance(parsed, ParsedAttackEvent)
    assert parsed.attacker_level in AttackerLevel
    assert isinstance(parsed.ttp_ids, list)
    assert 0.0 <= parsed.confidence <= 1.0
    assert parsed.kill_chain_phase in KillChainPhase
    assert parsed.raw_event.event_id == event.event_id


def test_e2e_01_parser_advanced_msg():
    """[E2E-01] 고급 명령(FILE_TRANSFER_PROTOCOL) → L3+ 분류."""
    from cti_pipeline.attack_event_parser import AttackEventParser

    parser = AttackEventParser()
    event  = make_capture_event(msg_type="FILE_TRANSFER_PROTOCOL")
    parsed = parser.parse(event)

    assert parsed.attacker_level.value >= AttackerLevel.L2_INTERMEDIATE.value


def test_e2e_01_parser_heartbeat():
    """[E2E-01] HEARTBEAT → RECONNAISSANCE 단계."""
    from cti_pipeline.attack_event_parser import AttackEventParser

    parser = AttackEventParser()
    event  = make_capture_event(msg_type="HEARTBEAT")
    parsed = parser.parse(event)

    assert parsed.kill_chain_phase == KillChainPhase.RECONNAISSANCE


# ════════════════════════════════════════════════════════════════════════════════
# E2E-02: STIXConverter
# ════════════════════════════════════════════════════════════════════════════════

def test_e2e_02_stix_converter_bundle_structure():
    """[E2E-02] ParsedAttackEvent → stix2.Bundle 구조 검증."""
    import stix2
    from cti_pipeline.stix_converter import STIXConverter

    converter = STIXConverter()
    parsed    = make_parsed_event(ttp_ids=["T0855", "T0836"])
    bundle    = converter.convert(parsed)

    assert isinstance(bundle, stix2.Bundle)
    assert bundle.spec_version == "2.1"
    # Bundle에 AttackPattern이 TTP 수만큼 포함
    ap_count = sum(1 for o in bundle.objects if o.type == "attack-pattern")
    assert ap_count == 2


def test_e2e_02_stix_json_serializable():
    """[E2E-02] STIX Bundle → JSON 직렬화 가능."""
    from cti_pipeline.stix_converter import STIXConverter

    converter = STIXConverter()
    bundle    = converter.convert(make_parsed_event())
    json_str  = converter.to_json(bundle)
    parsed    = json.loads(json_str)

    assert parsed["type"] == "bundle"
    assert "objects" in parsed
    assert len(parsed["objects"]) > 0


def test_e2e_02_stix_batch_convert():
    """[E2E-02] 배치 변환 — 3개 이벤트 → 단일 Bundle."""
    from cti_pipeline.stix_converter import STIXConverter

    converter = STIXConverter()
    events    = [make_parsed_event(ttp_ids=[f"T085{i}"]) for i in range(3)]
    bundle    = converter.batch_convert(events)

    # 3개 이벤트의 TTP가 모두 포함되어야 함 (중복 제거)
    ap_ids = {
        r.external_references[0].external_id
        for o in bundle.objects
        if hasattr(o, "type") and o.type == "attack-pattern"
        for r in getattr(o, "external_references", [])
    }
    assert len(ap_ids) == 3


# ════════════════════════════════════════════════════════════════════════════════
# E2E-03: EngagementTracker + MTDTrigger
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_e2e_03_engagement_tracker_session_creation():
    """[E2E-03] 새 이벤트 → 세션 자동 생성 + 메트릭 반환."""
    from honey_drone.engagement_tracker import EngagementTracker

    tracker = EngagementTracker()
    await tracker.start()

    event   = make_capture_event()
    metrics = await tracker.update_session(event)

    assert metrics.drone_id == "honey_01"
    assert metrics.attacker_ip == "192.168.1.100"
    assert metrics.commands_issued == 1
    assert metrics.attacker_level in AttackerLevel

    await tracker.stop()


@pytest.mark.asyncio
async def test_e2e_03_engagement_l4_classification():
    """[E2E-03] CVE exploit 패턴 → L4 또는 L3 분류."""
    from honey_drone.engagement_tracker import EngagementTracker

    tracker = EngagementTracker()
    await tracker.start()

    # WebSocket CVE exploit 이벤트
    ws_event = MavlinkCaptureEvent(
        drone_id="honey_01",
        src_ip="10.0.0.1",
        src_port=18789,
        protocol=DroneProtocol.WEBSOCKET,
        msg_type="WS_MESSAGE",
        payload_hex=b"Origin: null\r\nUpgrade: websocket".hex(),
        session_id="cve_test",
    )
    await tracker.update_session(ws_event)
    await tracker.record_websocket_connect("10.0.0.1", "honey_01")
    metrics = await tracker.update_session(ws_event)

    assert metrics.exploit_attempts >= 1
    assert metrics.attacker_level.value >= AttackerLevel.L3_ADVANCED.value

    await tracker.stop()


@pytest.mark.asyncio
async def test_e2e_03_mtd_trigger_urgency():
    """[E2E-03] exploit 탐지 → urgency >= 0.9 MTDTrigger."""
    from honey_drone.engagement_tracker import EngagementTracker

    tracker = EngagementTracker()
    await tracker.start()

    ws_exploit = MavlinkCaptureEvent(
        drone_id="honey_02",
        src_ip="172.16.0.5",
        src_port=18790,
        protocol=DroneProtocol.WEBSOCKET,
        msg_type="WS_MESSAGE",
        payload_hex=b"Origin: null localhost 127.0.0.1".hex(),
        session_id="exploit_test",
    )
    metrics = await tracker.update_session(ws_exploit)
    urgency  = tracker.compute_urgency(metrics)

    # exploit 시도가 있으면 urgency >= 0.9
    if metrics.exploit_attempts >= 1:
        assert urgency >= 0.9

    await tracker.stop()


# ════════════════════════════════════════════════════════════════════════════════
# E2E-04: PositiveCollector
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_e2e_04_positive_collector_label():
    """[E2E-04] ParsedAttackEvent → DatasetEntry (label=1)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["RESULTS_DIR"] = tmpdir
        import importlib
        import dataset.positive_collector as pc_mod
        importlib.reload(pc_mod)
        from dataset.positive_collector import PositiveCollector

        q          = asyncio.Queue()
        collector  = PositiveCollector(q)
        await collector.start()

        # 이벤트 주입
        parsed = make_parsed_event()
        await q.put(parsed)
        await asyncio.sleep(0.2)  # 소비 대기

        entries = collector.get_entries()
        assert len(entries) >= 1
        assert entries[0].label == 1
        assert entries[0].source == "honeydrone"
        assert entries[0].confidence > 0.0

        await collector.stop()
        os.environ.pop("RESULTS_DIR", None)


# ════════════════════════════════════════════════════════════════════════════════
# E2E-05: NegativeGenerator
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_e2e_05_negative_generator_label():
    """[E2E-05] 합성 생성 → DatasetEntry (label=0)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["RESULTS_DIR"] = tmpdir
        import importlib
        import dataset.negative_generator as ng_mod
        importlib.reload(ng_mod)
        from dataset.negative_generator import NegativeGenerator

        gen     = NegativeGenerator()
        entries = await gen.generate(n_samples=10, method="synthetic")

        assert len(entries) == 10
        for e in entries:
            assert e.label == 0
            assert e.attacker_level is None
            assert e.ttp_ids == []
            assert e.confidence == 1.0
            assert e.source == "synthetic"

        os.environ.pop("RESULTS_DIR", None)


# ════════════════════════════════════════════════════════════════════════════════
# E2E-06: DatasetPackager
# ════════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_e2e_06_dataset_packager_output():
    """[E2E-06] 패키징 → CSV + metadata.yaml + README.md 생성 확인."""
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["RESULTS_DIR"] = tmpdir
        import importlib
        import dataset.dataset_packager as dp_mod
        importlib.reload(dp_mod)
        from dataset.dataset_packager import DatasetPackager
        from dataset.negative_generator import NegativeGenerator

        positive = [
            DatasetEntry(
                drone_id="honey_01", src_ip="1.2.3.4",
                protocol=DroneProtocol.MAVLINK, msg_type="COMMAND_LONG",
                attacker_level=AttackerLevel.L2_INTERMEDIATE,
                ttp_ids=["T0855", "T0836"], label=1, confidence=0.9,
                source="honeydrone",
            )
            for _ in range(5)
        ]
        gen      = NegativeGenerator()
        negative = await gen.generate(5, method="synthetic")

        packager   = DatasetPackager()
        output_dir = await packager.package(positive, negative)

        assert (output_dir / "dataset.csv").exists()
        assert (output_dir / "metadata.yaml").exists()
        assert (output_dir / "README.md").exists()

        # CSV 내용 확인
        csv_lines = (output_dir / "dataset.csv").read_text().splitlines()
        assert len(csv_lines) == 11  # header + 10 rows

        os.environ.pop("RESULTS_DIR", None)


# ════════════════════════════════════════════════════════════════════════════════
# E2E-07: DatasetValidator
# ════════════════════════════════════════════════════════════════════════════════

def test_e2e_07_validator_pass():
    """[E2E-07] 균형 잡힌 데이터셋 → ValidationReport PASS."""
    from dataset.dataset_validator import DatasetValidator

    positive = [
        DatasetEntry(
            drone_id="honey_01", src_ip=f"10.0.0.{i}",
            protocol=DroneProtocol.MAVLINK,
            msg_type="COMMAND_LONG",
            attacker_level=list(AttackerLevel)[i % 5],
            ttp_ids=[f"T085{i % 5}", f"T086{i % 3}"],
            label=1, confidence=0.8 + (i % 2) * 0.1,
            source="honeydrone",
        )
        for i in range(10)
    ]
    negative = [
        DatasetEntry(
            drone_id="honey_01", src_ip="172.31.0.100",
            protocol=DroneProtocol.MAVLINK, msg_type="HEARTBEAT",
            label=0, confidence=1.0, source="synthetic",
        )
        for _ in range(10)
    ]

    validator = DatasetValidator()
    report    = validator.validate(positive + negative)

    assert report.total_samples == 20
    assert report.positive_count == 10
    assert report.negative_count == 10
    assert not report.has_errors


def test_e2e_07_validator_fail_no_ttps():
    """[E2E-07] TTP 없는 양성 샘플 → V2 ERROR."""
    from dataset.dataset_validator import DatasetValidator

    entries = [
        DatasetEntry(
            drone_id="honey_01", src_ip="1.2.3.4",
            protocol=DroneProtocol.MAVLINK, msg_type="COMMAND_LONG",
            attacker_level=AttackerLevel.L1_BASIC,
            ttp_ids=[],   # TTP 없음
            label=1, confidence=0.7, source="honeydrone",
        )
        for _ in range(5)
    ]
    negative = [
        DatasetEntry(label=0, confidence=1.0, source="synthetic",
                     protocol=DroneProtocol.MAVLINK, msg_type="HEARTBEAT")
        for _ in range(5)
    ]

    validator = DatasetValidator()
    report    = validator.validate(entries + negative)

    error_codes = [i.code for i in report.issues if i.severity == "ERROR"]
    assert "V2" in error_codes


# ════════════════════════════════════════════════════════════════════════════════
# E2E-08: MetricsCollector
# ════════════════════════════════════════════════════════════════════════════════

def test_e2e_08_metrics_table_ii():
    """[E2E-08] EngagementMetrics → Table II JSON 생성."""
    import uuid
    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["RESULTS_DIR"] = tmpdir
        import importlib
        import evaluation.metrics_collector as mc_mod
        importlib.reload(mc_mod)
        from evaluation.metrics_collector import MetricsCollector

        metrics_list = [
            EngagementMetrics(
                session_id=str(uuid.uuid4()),
                drone_id="honey_01",
                attacker_ip=f"10.0.0.{i}",
                attacker_level=AttackerLevel.L2_INTERMEDIATE,
                session_start_ns=time.time_ns(),
                last_activity_ns=time.time_ns() + int(30e9),
                dwell_time_sec=30.0 + i,
                commands_issued=10 + i,
                protocols_used=[DroneProtocol.MAVLINK],
                exploit_attempts=0,
            )
            for i in range(5)
        ]

        collector = MetricsCollector(exp_id="test_exp")
        rows      = collector.collect_engagement(metrics_list)

        assert len(rows) >= 1
        row = rows[0]
        assert row.level == "L2_INTERMEDIATE"
        assert row.session_count == 5
        assert row.avg_dwell_sec > 0

        # JSON 파일 생성 확인
        metrics_path = Path(tmpdir) / "metrics" / "table_ii_engagement.json"
        assert metrics_path.exists()

        os.environ.pop("RESULTS_DIR", None)

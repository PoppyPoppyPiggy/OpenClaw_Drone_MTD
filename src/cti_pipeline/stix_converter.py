#!/usr/bin/env python3
"""
stix_converter.py — STIX 2.1 변환기

Project  : MIRAGE-UAS
Module   : CTI Pipeline / STIX Converter
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[Inputs]
    - ParsedAttackEvent  (ATT&CK TTP 분류 완료)

[Outputs]
    - stix2.Bundle       → cti_ingest_api 경유 RL Agent / Dataset
    - str (JSON)         → 직렬화 후 파일 저장

[STIX 2.1 객체 구성]
    Bundle:
      ├── AttackPattern  (ATT&CK TTP ID, x-mitre-id)
      ├── Indicator      (network-traffic pattern)
      ├── ObservedData   (원시 패킷 관찰 기록)
      └── Note           (공격자 레벨, 신뢰도)

[Dependencies]
    - stix2 >= 3.0.1

[REF]
    STIX 2.1 Spec https://docs.oasis-open.org/cti/stix/v2.1/
    MITRE ATT&CK STIX https://github.com/mitre-attack/attack-stix-data
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import stix2

from shared.logger import get_logger
from shared.models import AttackerLevel, KillChainPhase, ParsedAttackEvent

logger = get_logger(__name__)

# ── MITRE ATT&CK STIX Identity (고정 참조 ID) ────────────────────────────────
# ATT&CK 공식 STIX 데이터의 identity ID
_MITRE_IDENTITY_ID = "identity--c78cb6e5-0c4b-4611-8297-d1b8b55e40b5"
_MITRE_IDENTITY    = stix2.Identity(
    id=_MITRE_IDENTITY_ID,
    name="The MITRE Corporation",
    identity_class="organization",
)

# MIRAGE-UAS 출처 Identity
_MIRAGE_IDENTITY = stix2.Identity(
    name="MIRAGE-UAS",
    identity_class="system",
    description="MIRAGE-UAS agentic honey drone CTI pipeline",
)

# ── Kill Chain Phase → STIX kill chain name 매핑 ──────────────────────────────
_KILL_CHAIN_MAP: dict[KillChainPhase, str] = {
    KillChainPhase.RECONNAISSANCE : "reconnaissance",
    KillChainPhase.WEAPONIZATION  : "weaponization",
    KillChainPhase.DELIVERY       : "delivery",
    KillChainPhase.EXPLOITATION   : "exploitation",
    KillChainPhase.INSTALLATION   : "installation",
    KillChainPhase.C2             : "command-and-control",
    KillChainPhase.ACTION         : "actions-on-objectives",
}


class STIXConverter:
    """
    [ROLE] ParsedAttackEvent를 STIX 2.1 Bundle로 변환.
           Track B CTI 파이프라인의 핵심 직렬화 계층.
           생성된 Bundle은 Dataset(Track B)과 CTI API 모두에 전달됨.

    [DATA FLOW]
        ParsedAttackEvent ──▶ convert(event) ──▶ stix2.Bundle
        stix2.Bundle ──▶ to_json()           ──▶ JSON str (파일 저장 / API 전송)
        list[ParsedAttackEvent] ──▶ batch_convert() ──▶ stix2.Bundle (묶음)
    """

    def convert(self, event: ParsedAttackEvent) -> stix2.Bundle:
        """
        [ROLE] 단일 ParsedAttackEvent → STIX 2.1 Bundle 변환.

        [DATA FLOW]
            ParsedAttackEvent
            ──▶ _build_attack_patterns() ──▶ list[AttackPattern]
            ──▶ _build_indicator()        ──▶ Indicator
            ──▶ _build_observed_data()    ──▶ ObservedData
            ──▶ _build_note()             ──▶ Note
            ──▶ stix2.Bundle(all objects)
        """
        ts = _event_ts(event.raw_event.timestamp_ns)

        attack_patterns = self._build_attack_patterns(event, ts)
        indicator       = self._build_indicator(event, ts, attack_patterns)
        observed_data   = self._build_observed_data(event, ts)
        note            = self._build_note(event, ts)

        objects = [
            _MITRE_IDENTITY,
            _MIRAGE_IDENTITY,
            *attack_patterns,
            indicator,
            observed_data,
            note,
        ]
        # stix2 v21 module infers spec_version; allow_custom for x_mirage_* props
        bundle = stix2.Bundle(objects=objects, allow_custom=True)
        logger.debug(
            "stix_bundle_created",
            bundle_id=bundle.id,
            ttp_count=len(attack_patterns),
            event_id=event.raw_event.event_id[:8],
        )
        return bundle

    def batch_convert(self, events: list[ParsedAttackEvent]) -> stix2.Bundle:
        """
        [ROLE] 복수 ParsedAttackEvent를 단일 Bundle로 일괄 변환.
               Dataset 패키징 시 에피소드 단위로 묶음.

        [DATA FLOW]
            list[ParsedAttackEvent]
            ──▶ 각 이벤트 STIX 객체 누적
            ──▶ 중복 AttackPattern ID 제거
            ──▶ stix2.Bundle
        """
        all_objects: dict[str, any] = {
            _MITRE_IDENTITY.id  : _MITRE_IDENTITY,
            _MIRAGE_IDENTITY.id : _MIRAGE_IDENTITY,
        }
        for event in events:
            ts = _event_ts(event.raw_event.timestamp_ns)
            for ap in self._build_attack_patterns(event, ts):
                all_objects[ap.id] = ap
            ind  = self._build_indicator(event, ts, list(all_objects.values()))
            obs  = self._build_observed_data(event, ts)
            note = self._build_note(event, ts)
            for obj in (ind, obs, note):
                all_objects[obj.id] = obj

        bundle = stix2.Bundle(
            objects=list(all_objects.values()),
            allow_custom=True,
        )
        logger.info(
            "stix_batch_bundle_created",
            bundle_id=bundle.id,
            event_count=len(events),
            object_count=len(all_objects),
        )
        return bundle

    def to_json(self, bundle: stix2.Bundle) -> str:
        """
        [ROLE] stix2.Bundle → JSON 문자열 직렬화 (파일 저장 / API 전송용).

        [DATA FLOW]
            stix2.Bundle ──▶ bundle.serialize(pretty=True) ──▶ str
        """
        return bundle.serialize(pretty=True)

    # ── 내부 빌더 ─────────────────────────────────────────────────────────────

    def _build_attack_patterns(
        self, event: ParsedAttackEvent, ts: datetime
    ) -> list[stix2.AttackPattern]:
        """
        [ROLE] TTP ID 목록 → STIX AttackPattern 객체 리스트 생성.
               ATT&CK for ICS v14 x-mitre-id 포함.

        [DATA FLOW]
            event.ttp_ids ──▶ [AttackPattern(ttp_id) for each id]
        """
        phase_name = _KILL_CHAIN_MAP.get(event.kill_chain_phase, "unknown")
        patterns   = []
        for ttp_id in event.ttp_ids:
            ap = stix2.AttackPattern(
                created=ts,
                modified=ts,
                name=f"ATT&CK ICS {ttp_id}",
                description=f"MITRE ATT&CK for ICS technique {ttp_id} "
                            f"observed in UAS MAVLink traffic",
                kill_chain_phases=[
                    stix2.KillChainPhase(
                        kill_chain_name="mitre-attack-ics",
                        phase_name=phase_name,
                    )
                ],
                external_references=[
                    stix2.ExternalReference(
                        source_name="mitre-attack-ics",
                        external_id=ttp_id,
                        url=f"https://attack.mitre.org/techniques/{ttp_id}/",
                    )
                ],
                custom_properties={
                    "x_mitre_id"             : ttp_id,
                    "x_mirage_attacker_level": event.attacker_level.name,
                    "x_mirage_drone_id"      : event.raw_event.drone_id,
                    "x_mirage_confidence"    : event.confidence,
                },
            )
            patterns.append(ap)
        return patterns

    def _build_indicator(
        self,
        event: ParsedAttackEvent,
        ts: datetime,
        related_objects: list,
    ) -> stix2.Indicator:
        """
        [ROLE] 네트워크 트래픽 패턴을 STIX Indicator로 표현.
               공격자 IP + 프로토콜 + 메시지 유형 조합으로 패턴 생성.

        [DATA FLOW]
            event 네트워크 정보 ──▶ STIX pattern ──▶ Indicator
        """
        proto    = event.raw_event.protocol.value
        msg_type = event.raw_event.msg_type
        src_ip   = event.raw_event.src_ip

        # STIX 2.1 compliant pattern (§9.3 STIX Patterning)
        # References use STIX Cyber-observable Object properties directly
        pattern = (
            f"[ipv4-addr:value = '{src_ip}'] AND "
            f"[network-traffic:dst_port = {event.raw_event.src_port} AND "
            f"network-traffic:protocols[0] = '{proto}']"
        )

        return stix2.Indicator(
            created=ts,
            modified=ts,
            name=f"MIRAGE-UAS: {proto}/{msg_type} from {src_ip}",
            description=(
                f"Malicious {proto.upper()} traffic observed on honey drone "
                f"{event.raw_event.drone_id}. "
                f"Attacker level: {event.attacker_level.name}. "
                f"TTPs: {', '.join(event.ttp_ids)}"
            ),
            pattern=pattern,
            pattern_type="stix",
            valid_from=ts,
            confidence=int(event.confidence * 100),
            labels=["malicious-activity", "drone-attack", proto],
            custom_properties={
                "x_mirage_attacker_level": event.attacker_level.name,
                "x_mirage_drone_id"      : event.raw_event.drone_id,
                "x_mirage_msg_type"      : msg_type,
                "x_mirage_ttp_ids"       : event.ttp_ids,
            },
        )

    def _build_observed_data(
        self, event: ParsedAttackEvent, ts: datetime
    ) -> stix2.ObservedData:
        """
        [ROLE] 원시 패킷 관찰 기록을 STIX ObservedData로 표현.
               Dataset 재현성과 연구 감사를 위한 raw evidence 보존.

        [DATA FLOW]
            event.raw_event ──▶ network-traffic SCO ──▶ ObservedData
        """
        ipv4_src = stix2.IPv4Address(value=event.raw_event.src_ip)
        net_traffic = stix2.NetworkTraffic(
            src_ref=ipv4_src.id,
            dst_port=event.raw_event.src_port,
            protocols=[event.raw_event.protocol.value],
            custom_properties={
                "x_mirage_payload_hex": event.raw_event.payload_hex[:256],
                "x_mirage_msg_id"     : event.raw_event.msg_id,
                "x_mirage_msg_type"   : event.raw_event.msg_type,
            },
        )
        # STIX 2.1 §5.7: object_refs MUST include ALL referenced SCOs
        return stix2.ObservedData(
            created=ts,
            modified=ts,
            first_observed=ts,
            last_observed=ts,
            number_observed=1,
            object_refs=[ipv4_src.id, net_traffic.id],
        )

    def _build_note(
        self, event: ParsedAttackEvent, ts: datetime
    ) -> stix2.Note:
        """
        [ROLE] 공격자 분류 메타데이터를 STIX Note로 기록.
               논문 재현성을 위한 분류 근거 보존.

        [DATA FLOW]
            event 분류 결과 ──▶ Note (confidence, level, kill_chain)
        """
        return stix2.Note(
            created=ts,
            modified=ts,
            content=(
                f"MIRAGE-UAS Classification Result\n"
                f"Attacker Level : {event.attacker_level.name} "
                f"({event.attacker_level.value})\n"
                f"Kill Chain     : {event.kill_chain_phase.value}\n"
                f"Confidence     : {event.confidence:.3f}\n"
                f"Dwell Time     : {event.dwell_time_sec:.1f}s\n"
                f"TTPs           : {', '.join(event.ttp_ids)}\n"
                f"Drone ID       : {event.raw_event.drone_id}"
            ),
            authors=["MIRAGE-UAS"],
            object_refs=[_MIRAGE_IDENTITY.id],
        )


# ── 헬퍼 함수 ─────────────────────────────────────────────────────────────────

def _event_ts(timestamp_ns: int) -> datetime:
    """[ROLE] Unix ns 타임스탬프 → timezone-aware datetime 변환."""
    return datetime.fromtimestamp(timestamp_ns / 1e9, tz=timezone.utc)

#!/usr/bin/env python3
"""
attack_event_parser.py — 공격 이벤트 파서

Project  : MIRAGE-UAS
Module   : CTI Pipeline / Attack Event Parser
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[Inputs]
    - asyncio.Queue[MavlinkCaptureEvent]  (MavlinkInterceptor 출력)

[Outputs]
    - asyncio.Queue[ParsedAttackEvent]    → STIXConverter (Phase B2)

[설계 원칙]
    - MavlinkCaptureEvent를 파싱하여 ATT&CK TTP + L0-L4 분류
    - 세션 내 이벤트 축적으로 attacker level 재계산 (시간 윈도우 기반)
    - is_anomalous 이벤트는 높은 confidence로 공격 분류
    - 파서는 stateful: 세션별 이벤트 히스토리 유지

[DATA FLOW]
    asyncio.Queue[MavlinkCaptureEvent]
    ──▶ AttackEventParser.run()
    ──▶ _classify_event()
        ├── ATTCKMapper.map_event() ──▶ ttp_ids, kill_chain, confidence
        └── _compute_attacker_level() ──▶ AttackerLevel
    ──▶ ParsedAttackEvent
    ──▶ asyncio.Queue[ParsedAttackEvent] (STIXConverter)
"""

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field

from shared.logger import get_logger
from shared.models import (
    AttackerLevel,
    DroneProtocol,
    KillChainPhase,
    MavlinkCaptureEvent,
    ParsedAttackEvent,
)
from cti_pipeline.attck_mapper import get_mapper

logger = get_logger(__name__)

# 세션 집계 윈도우 (초) — 이 시간 내 이벤트를 동일 세션으로 묶음
_SESSION_WINDOW_SEC: float = 120.0
# 세션 내 이벤트 히스토리 최대 개수 (메모리 제한)
_MAX_SESSION_HISTORY: int = 500


# ── 세션 집계 레코드 (내부 사용) ──────────────────────────────────────────────
@dataclass
class _SessionAccumulator:
    session_id:    str
    drone_id:      str
    first_seen_ns: int = field(default_factory=time.time_ns)
    last_seen_ns:  int = field(default_factory=time.time_ns)
    event_count:   int = 0
    anomaly_count: int = 0
    ttp_ids_seen:  set[str] = field(default_factory=set)
    phases_seen:   set[KillChainPhase] = field(default_factory=set)
    protocols:     set[DroneProtocol] = field(default_factory=set)

    @property
    def dwell_time_sec(self) -> float:
        return (self.last_seen_ns - self.first_seen_ns) / 1e9


class AttackEventParser:
    """
    [ROLE] MavlinkCaptureEvent 스트림을 소비하여 ParsedAttackEvent로 변환.
           세션별 이벤트를 축적하여 L0-L4 분류 정밀도 향상.

    [DATA FLOW]
        asyncio.Queue[MavlinkCaptureEvent]
        ──▶ _classify_event()
        ──▶ asyncio.Queue[ParsedAttackEvent]
    """

    def __init__(
        self,
        input_queue: asyncio.Queue,
        output_queue: asyncio.Queue,
    ) -> None:
        self._input_q  = input_queue
        self._output_q = output_queue
        self._mapper   = get_mapper()
        # session_id → _SessionAccumulator
        self._sessions: dict[str, _SessionAccumulator] = {}

    async def run(self) -> None:
        """
        [ROLE] 입력 큐를 지속 소비하며 파싱 실행.

        [DATA FLOW]
            input_queue.get() ──▶ _classify_event() ──▶ output_queue.put()
        """
        logger.info("attack_event_parser started")
        while True:
            try:
                event: MavlinkCaptureEvent = await self._input_q.get()
                parsed = self._classify_event(event)
                await self._output_q.put(parsed)
                self._input_q.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("parser error", error=str(e))

    def _classify_event(self, event: MavlinkCaptureEvent) -> ParsedAttackEvent:
        """
        [ROLE] 단일 MavlinkCaptureEvent를 ParsedAttackEvent로 분류.
               세션 누적 컨텍스트를 활용하여 L0-L4 분류 정밀도 향상.

        [DATA FLOW]
            MavlinkCaptureEvent
            ──▶ ATTCKMapper.map_event() ──▶ ttp_ids, kill_chain, base_confidence
            ──▶ _update_accumulator()   ──▶ 세션 컨텍스트 갱신
            ──▶ _compute_level()        ──▶ AttackerLevel
            ──▶ _adjust_confidence()    ──▶ 최종 confidence
            ──▶ ParsedAttackEvent
        """
        # 1. ATT&CK TTP 매핑
        ttp_ids, kill_chain, base_conf = self._mapper.map_event(
            protocol=event.protocol,
            msg_type=event.msg_type,
            http_method=event.http_method,
            http_path=event.http_path,
        )

        # 2. 이상 패킷 신뢰도 보정
        if event.is_anomalous:
            base_conf = min(base_conf + 0.15, 1.0)
            # 이상 패킷에 추가 TTP 보강
            if "T0855" not in ttp_ids:
                ttp_ids = ttp_ids + ["T0855"]

        # 3. 세션 누적 컨텍스트 갱신
        acc = self._update_accumulator(event, ttp_ids, kill_chain)

        # 4. L0-L4 분류 (세션 컨텍스트 기반)
        level = self._compute_level(acc)

        # 5. 세션 컨텍스트 기반 confidence 조정
        final_conf = self._adjust_confidence(base_conf, acc, level)

        parsed = ParsedAttackEvent(
            raw_event=event,
            attacker_level=level,
            ttp_ids=ttp_ids,
            kill_chain_phase=kill_chain,
            confidence=final_conf,
            dwell_time_sec=acc.dwell_time_sec,
        )

        logger.debug(
            "event_parsed",
            event_id=event.event_id[:8],
            drone_id=event.drone_id,
            msg_type=event.msg_type,
            ttps=ttp_ids,
            level=level.name,
            conf=round(final_conf, 3),
        )
        return parsed

    def _update_accumulator(
        self,
        event: MavlinkCaptureEvent,
        ttp_ids: list[str],
        kill_chain: KillChainPhase,
    ) -> _SessionAccumulator:
        """
        [ROLE] 세션 누적 레코드 생성 또는 갱신.
               세션 윈도우 초과 시 자동 리셋.

        [DATA FLOW]
            event.session_id ──▶ 기존 세션 조회 또는 신규 생성
            ──▶ ttp_ids / phases_seen / protocols 누적
            ──▶ _SessionAccumulator
        """
        sid = event.session_id or event.event_id

        if sid not in self._sessions:
            self._sessions[sid] = _SessionAccumulator(
                session_id=sid,
                drone_id=event.drone_id,
            )
        acc = self._sessions[sid]

        # 윈도우 초과 시 리셋 (동일 세션 ID가 재사용될 때)
        elapsed = (time.time_ns() - acc.last_seen_ns) / 1e9
        if elapsed > _SESSION_WINDOW_SEC:
            self._sessions[sid] = _SessionAccumulator(
                session_id=sid,
                drone_id=event.drone_id,
            )
            acc = self._sessions[sid]

        # 누적
        acc.last_seen_ns = time.time_ns()
        acc.event_count += 1
        if event.is_anomalous:
            acc.anomaly_count += 1
        acc.ttp_ids_seen.update(ttp_ids)
        acc.phases_seen.add(kill_chain)
        acc.protocols.add(event.protocol)

        # 히스토리 크기 제한 (메모리 보호)
        if len(self._sessions) > _MAX_SESSION_HISTORY:
            oldest = min(self._sessions.items(), key=lambda x: x[1].last_seen_ns)
            del self._sessions[oldest[0]]

        return acc

    def _compute_level(self, acc: _SessionAccumulator) -> AttackerLevel:
        """
        [ROLE] 세션 누적 컨텍스트 기반 L0-L4 공격자 레벨 분류.
               단일 이벤트가 아닌 세션 전체 패턴으로 판단.

        [DATA FLOW]
            _SessionAccumulator ──▶ 규칙 매칭 ──▶ AttackerLevel
        """
        ev  = acc.event_count
        anom = acc.anomaly_count
        ttps = len(acc.ttp_ids_seen)
        phs  = len(acc.phases_seen)
        proto = len(acc.protocols)
        dwell = acc.dwell_time_sec

        # L4: 다단계 공격 + 다중 프로토콜 + 장시간 체류
        if ev >= 30 and anom >= 5 and ttps >= 5 and proto >= 3 and dwell >= 60:
            return AttackerLevel.L4_APT

        # L3: 다단계 + exploit 포함
        if ev >= 15 and anom >= 2 and ttps >= 3 and phs >= 2:
            return AttackerLevel.L3_ADVANCED

        # L2: 중간 규모 + 취약점 타겟팅
        if ev >= 8 and (anom >= 1 or ttps >= 2) and dwell >= 10:
            return AttackerLevel.L2_INTERMEDIATE

        # L1: 기본 프로토콜 인식
        if ev >= 3 or ttps >= 1:
            return AttackerLevel.L1_BASIC

        return AttackerLevel.L0_SCRIPT_KIDDIE

    def _adjust_confidence(
        self,
        base_conf: float,
        acc: _SessionAccumulator,
        level: AttackerLevel,
    ) -> float:
        """
        [ROLE] 세션 컨텍스트 기반 confidence 조정.
               이벤트 수가 많을수록, anomaly 비율이 높을수록 신뢰도 상승.

        [DATA FLOW]
            base_conf + (이벤트 수 보정 + anomaly 비율 보정) ──▶ [0.0, 1.0]
        """
        # 이벤트 수 보정: 10개 이상이면 +0.1
        count_bonus = 0.10 if acc.event_count >= 10 else 0.0
        # anomaly 비율 보정
        anom_ratio  = acc.anomaly_count / max(acc.event_count, 1)
        anom_bonus  = 0.10 * anom_ratio
        # L3/L4 레벨 보정
        level_bonus = 0.05 if level >= AttackerLevel.L3_ADVANCED else 0.0

        return min(base_conf + count_bonus + anom_bonus + level_bonus, 1.0)

    def get_session_stats(self) -> dict:
        """
        [ROLE] 논문 §6 통계용 현재 세션 요약 반환.

        [DATA FLOW]
            _sessions dict ──▶ 집계 통계 ──▶ dict
        """
        return {
            "active_sessions":   len(self._sessions),
            "total_events":      sum(a.event_count for a in self._sessions.values()),
            "total_anomalies":   sum(a.anomaly_count for a in self._sessions.values()),
            "unique_ttps_seen":  len(set().union(*[a.ttp_ids_seen for a in self._sessions.values()]) if self._sessions else set()),
        }

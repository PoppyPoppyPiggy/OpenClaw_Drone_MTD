#!/usr/bin/env python3
"""
strategic_directive.py — Tier 1 → Tier 2 communication schema

[ROLE]
    Defines the StrategicDirective dataclass emitted by GCS StrategicAgent
    and consumed by OpenClawAgent's tactical LLM / DQN policy.

[WIRE FORMAT]
    JSON over UDP 127.0.0.1:19995, fire-and-forget, no ACK.
    (Symmetric with existing event ports 19996-19999.)

[FLOW]
    GCS StrategicAgent (every STRATEGIC_INTERVAL_SEC)
      ──▶ observe() from snapshot / 19998 event stream
      ──▶ LLM decides strategic response
      ──▶ emit_directive(directive) → UDP 19995
                                        │
    Each drone's OpenClawAgent:         ▼
      ──▶ _directive_listener_loop()  (UDP server on 19995)
      ──▶ filter by target_drone_id
      ──▶ update self._last_directive
      ──▶ _build_mab_context() exposes directive fields
      ──▶ LLMTacticalAgent / DQN picks skill biased by directive

[REF] MIRAGE-UAS §4.3 — Hierarchical Control
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Optional

STRATEGIC_DIRECTIVE_PORT: int = 19995


@dataclass
class StrategicDirective:
    """
    One strategic directive from GCS Tier 1 to a honeydrone Tier 2.

    Fields:
        target_drone_id : "*" for broadcast, otherwise drone_id.
        issued_at       : epoch seconds when GCS emitted (wall clock).
        action          : one of {deploy_decoy, rotate_identity, escalate,
                                  de_escalate, alert_operator, observe}.
        skill_bias      : optional soft bias — mapping skill_idx -> weight,
                          passed as hint to tactical LLM prompt or used to
                          reweight DQN Q-values.
        urgency         : [0, 1] — higher = tighter proactive interval.
        reason          : human-readable rationale (≤240 chars).
        ttl_sec         : directive expires after this many seconds;
                          consumer must drop stale directives.
    """

    target_drone_id: str
    issued_at: float
    action: str
    skill_bias: dict = field(default_factory=dict)
    urgency: float = 0.5
    reason: str = ""
    ttl_sec: float = 30.0

    def to_json_bytes(self) -> bytes:
        return json.dumps(asdict(self), default=str).encode("utf-8")

    @classmethod
    def from_json_bytes(cls, raw: bytes) -> Optional["StrategicDirective"]:
        try:
            payload = json.loads(raw.decode("utf-8", errors="replace"))
            bias_raw = payload.get("skill_bias") or {}
            bias = {int(k): float(v) for k, v in bias_raw.items()}
            return cls(
                target_drone_id=str(payload.get("target_drone_id", "*")),
                issued_at=float(payload.get("issued_at", 0.0)),
                action=str(payload.get("action", "observe")),
                skill_bias=bias,
                urgency=float(payload.get("urgency", 0.5)),
                reason=str(payload.get("reason", ""))[:240],
                ttl_sec=float(payload.get("ttl_sec", 30.0)),
            )
        except Exception:
            return None

    def is_fresh(self, now: float) -> bool:
        return (now - self.issued_at) < self.ttl_sec

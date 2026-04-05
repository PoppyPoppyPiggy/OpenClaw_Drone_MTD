#!/usr/bin/env python3
"""
dataset_validator.py — DVD-CTI-Dataset-v1 품질 검증기

Project  : MIRAGE-UAS
Module   : Dataset / Validator
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[Inputs]
    - list[DatasetEntry] (DatasetPackager 이후 검증)
    - results/dataset/DVD-CTI-Dataset-v1/dataset.csv

[Outputs]
    - ValidationReport (dataclass)
    - results/dataset/validation_report.json

[검증 항목]
    V1: 클래스 불균형 (양성:음성 비율 ≥ 1:10 경고)
    V2: ATT&CK TTP 커버리지 (최소 5개 TTP 필요)
    V3: 중복 entry_id 탐지
    V4: 신뢰도 유효 범위 [0.0, 1.0]
    V5: 양성 샘플 L0-L4 분포 (단일 레벨 90% 초과 경고)
    V6: 프로토콜 커버리지 (MAVLink + 최소 1개 추가 프로토콜)
"""

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from shared.constants import RESULTS_DIR
from shared.logger import get_logger
from shared.models import AttackerLevel, DatasetEntry, DroneProtocol

logger = get_logger(__name__)

_VALIDATION_REPORT_PATH = Path(RESULTS_DIR) / "dataset" / "validation_report.json"

# 검증 임계값 (논문 §6 데이터셋 품질 기준)
_MIN_TTP_COUNT         : int   = 5      # 최소 고유 TTP 수
_MAX_CLASS_IMBALANCE   : float = 10.0   # 음성:양성 최대 비율
_MAX_SINGLE_LEVEL_RATIO: float = 0.90   # 단일 공격자 레벨 최대 비율
_MIN_CONFIDENCE        : float = 0.0
_MAX_CONFIDENCE        : float = 1.0


@dataclass
class ValidationIssue:
    code     : str    # V1~V6
    severity : str    # "ERROR" | "WARNING"
    message  : str

    def __repr__(self) -> str:
        return f"[{self.severity}] {self.code}: {self.message}"


@dataclass
class ValidationReport:
    """
    [ROLE] 데이터셋 검증 결과 요약.
           논문 §6 데이터 품질 검증 근거.
    """
    total_samples  : int   = 0
    positive_count : int   = 0
    negative_count : int   = 0
    unique_ttps    : list[str] = field(default_factory=list)
    duplicate_ids  : list[str] = field(default_factory=list)
    issues         : list[ValidationIssue] = field(default_factory=list)
    passed         : bool  = False
    validated_at   : float = field(default_factory=time.time)

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "ERROR" for i in self.issues)

    @property
    def class_ratio(self) -> float:
        return self.negative_count / max(self.positive_count, 1)

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        errors   = sum(1 for i in self.issues if i.severity == "ERROR")
        warnings = sum(1 for i in self.issues if i.severity == "WARNING")
        return (
            f"ValidationReport [{status}] "
            f"total={self.total_samples} "
            f"pos={self.positive_count} neg={self.negative_count} "
            f"ttps={len(self.unique_ttps)} "
            f"errors={errors} warnings={warnings}"
        )


class DatasetValidator:
    """
    [ROLE] DatasetEntry 목록에 대해 논문 품질 기준 검증 수행.

    [DATA FLOW]
        list[DatasetEntry] ──▶ validate() ──▶ ValidationReport
        ValidationReport   ──▶ save_report() ──▶ validation_report.json
    """

    def validate(self, entries: list[DatasetEntry]) -> ValidationReport:
        """
        [ROLE] 전체 검증 항목 V1-V6 실행 후 ValidationReport 반환.

        [DATA FLOW]
            list[DatasetEntry]
            ──▶ V1 클래스 균형
            ──▶ V2 TTP 커버리지
            ──▶ V3 중복 탐지
            ──▶ V4 신뢰도 범위
            ──▶ V5 레벨 분포
            ──▶ V6 프로토콜 커버리지
            ──▶ ValidationReport
        """
        positive = [e for e in entries if e.label == 1]
        negative = [e for e in entries if e.label == 0]

        all_ttps: set[str] = set()
        for e in positive:
            all_ttps.update(e.ttp_ids)

        report = ValidationReport(
            total_samples=len(entries),
            positive_count=len(positive),
            negative_count=len(negative),
            unique_ttps=sorted(all_ttps),
        )

        self._check_class_balance(report, positive, negative)
        self._check_ttp_coverage(report, all_ttps)
        self._check_duplicates(report, entries)
        self._check_confidence_range(report, entries)
        self._check_level_distribution(report, positive)
        self._check_protocol_coverage(report, entries)

        report.passed = not report.has_errors
        logger.info(report.summary())
        return report

    def save_report(self, report: ValidationReport) -> None:
        """
        [ROLE] ValidationReport를 JSON으로 저장 (논문 첨부 근거 자료).

        [DATA FLOW]
            ValidationReport ──▶ dict ──▶ validation_report.json
        """
        _VALIDATION_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "total_samples"  : report.total_samples,
            "positive_count" : report.positive_count,
            "negative_count" : report.negative_count,
            "class_ratio"    : round(report.class_ratio, 3),
            "unique_ttps"    : report.unique_ttps,
            "ttp_count"      : len(report.unique_ttps),
            "duplicate_ids"  : report.duplicate_ids,
            "passed"         : report.passed,
            "validated_at"   : report.validated_at,
            "issues"         : [
                {"code": i.code, "severity": i.severity, "message": i.message}
                for i in report.issues
            ],
        }
        _VALIDATION_REPORT_PATH.write_text(
            json.dumps(data, indent=2, ensure_ascii=False)
        )
        logger.info("validation_report_saved", path=str(_VALIDATION_REPORT_PATH))

    # ── 검증 항목 ─────────────────────────────────────────────────────────────

    def _check_class_balance(
        self,
        report: ValidationReport,
        positive: list[DatasetEntry],
        negative: list[DatasetEntry],
    ) -> None:
        """[V1] 클래스 불균형 검사."""
        if not positive:
            report.issues.append(ValidationIssue(
                "V1", "ERROR", "양성 샘플이 없습니다."
            ))
            return
        ratio = len(negative) / len(positive)
        if ratio > _MAX_CLASS_IMBALANCE:
            report.issues.append(ValidationIssue(
                "V1", "WARNING",
                f"클래스 불균형 심각: negative/positive = {ratio:.1f} (권장 ≤ {_MAX_CLASS_IMBALANCE})"
            ))
        else:
            logger.debug("V1 class_balance OK", ratio=round(ratio, 2))

    def _check_ttp_coverage(
        self, report: ValidationReport, ttps: set[str]
    ) -> None:
        """[V2] ATT&CK TTP 커버리지 검사."""
        if len(ttps) < _MIN_TTP_COUNT:
            report.issues.append(ValidationIssue(
                "V2", "ERROR",
                f"TTP 커버리지 부족: {len(ttps)}개 (최소 {_MIN_TTP_COUNT}개 필요)"
            ))
        else:
            logger.debug("V2 ttp_coverage OK", count=len(ttps))

    def _check_duplicates(
        self, report: ValidationReport, entries: list[DatasetEntry]
    ) -> None:
        """[V3] 중복 entry_id 탐지."""
        seen: set[str] = set()
        dups: list[str] = []
        for e in entries:
            if e.entry_id in seen:
                dups.append(e.entry_id)
            seen.add(e.entry_id)
        report.duplicate_ids = dups
        if dups:
            report.issues.append(ValidationIssue(
                "V3", "WARNING",
                f"중복 entry_id {len(dups)}개 발견"
            ))
        else:
            logger.debug("V3 no_duplicates OK")

    def _check_confidence_range(
        self, report: ValidationReport, entries: list[DatasetEntry]
    ) -> None:
        """[V4] 신뢰도 [0.0, 1.0] 범위 검사."""
        out_of_range = [
            e.entry_id for e in entries
            if not (_MIN_CONFIDENCE <= e.confidence <= _MAX_CONFIDENCE)
        ]
        if out_of_range:
            report.issues.append(ValidationIssue(
                "V4", "ERROR",
                f"신뢰도 범위 이탈 {len(out_of_range)}개 (entry_ids: {out_of_range[:3]}...)"
            ))
        else:
            logger.debug("V4 confidence_range OK")

    def _check_level_distribution(
        self, report: ValidationReport, positive: list[DatasetEntry]
    ) -> None:
        """[V5] 공격자 레벨 분포 편중 검사."""
        if not positive:
            return
        level_counts: dict[str, int] = {}
        for e in positive:
            lv = e.attacker_level.name if e.attacker_level else "unknown"
            level_counts[lv] = level_counts.get(lv, 0) + 1
        max_ratio = max(level_counts.values()) / len(positive)
        if max_ratio > _MAX_SINGLE_LEVEL_RATIO:
            dominant = max(level_counts, key=lambda k: level_counts[k])
            report.issues.append(ValidationIssue(
                "V5", "WARNING",
                f"레벨 분포 편중: {dominant} = {max_ratio:.1%} "
                f"(권장 ≤ {_MAX_SINGLE_LEVEL_RATIO:.0%})"
            ))
        else:
            logger.debug("V5 level_distribution OK", dist=level_counts)

    def _check_protocol_coverage(
        self, report: ValidationReport, entries: list[DatasetEntry]
    ) -> None:
        """[V6] 프로토콜 커버리지 검사 (MAVLink + 최소 1개 추가)."""
        protocols = {e.protocol for e in entries}
        if DroneProtocol.MAVLINK not in protocols:
            report.issues.append(ValidationIssue(
                "V6", "ERROR", "MAVLink 프로토콜 샘플 없음"
            ))
        elif len(protocols) < 2:
            report.issues.append(ValidationIssue(
                "V6", "WARNING",
                "MAVLink 외 프로토콜 미포함 (HTTP/RTSP/WebSocket 권장)"
            ))
        else:
            logger.debug("V6 protocol_coverage OK", protocols={p.value for p in protocols})

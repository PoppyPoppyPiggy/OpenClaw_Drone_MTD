#!/usr/bin/env python3
"""
dataset_packager.py — DVD-CTI-Dataset-v1 패키저

Project  : MIRAGE-UAS
Module   : Dataset / Packager
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[Inputs]
    - list[DatasetEntry] positive  (PositiveCollector)
    - list[DatasetEntry] negative  (NegativeGenerator)

[Outputs]
    - results/dataset/DVD-CTI-Dataset-v1/
        ├── dataset.csv              (전체 레이블 데이터)
        ├── metadata.yaml            (데이터셋 메타데이터)
        ├── stix_bundles/            (공격 이벤트 STIX 번들)
        │   └── bundle_*.json
        └── README.md

[논문 기여]
    세계 최초 honeypot-derived labeled UAS CTI dataset
    ATT&CK for ICS v14 기반 TTP 레이블 포함
    REF: MIRAGE-UAS §6, C2
"""

import csv
import json
import time
import uuid
from pathlib import Path

import aiofiles
import yaml

from shared.constants import RESULTS_DIR
from shared.logger import get_logger
from shared.models import DatasetEntry

logger = get_logger(__name__)

_DATASET_NAME    = "DVD-CTI-Dataset"
_DATASET_VERSION = "v1"
_OUTPUT_DIR      = Path(RESULTS_DIR) / "dataset" / f"{_DATASET_NAME}-{_DATASET_VERSION}"


class DatasetPackager:
    """
    [ROLE] 양성/음성 DatasetEntry를 결합하여 논문 공개용 데이터셋 패키지 생성.

    [DATA FLOW]
        list[DatasetEntry] positive + negative
        ──▶ package()
        ──▶ _write_csv()      ──▶ dataset.csv
        ──▶ _write_metadata() ──▶ metadata.yaml
        ──▶ _write_readme()   ──▶ README.md
    """

    async def package(
        self,
        positive: list[DatasetEntry],
        negative: list[DatasetEntry],
    ) -> Path:
        """
        [ROLE] 양성/음성 샘플 결합 후 데이터셋 패키지 디렉토리 생성.

        [DATA FLOW]
            positive + negative ──▶ shuffle + merge
            ──▶ CSV + YAML + README
            ──▶ 출력 디렉토리 경로 반환
        """
        _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (_OUTPUT_DIR / "stix_bundles").mkdir(exist_ok=True)

        # 결합 + 셔플
        all_entries = positive + negative
        import random
        random.shuffle(all_entries)

        await self._write_csv(all_entries)
        await self._write_metadata(positive, negative)
        await self._write_readme(positive, negative)

        logger.info(
            "dataset_packaged",
            output_dir=str(_OUTPUT_DIR),
            total=len(all_entries),
            positive=len(positive),
            negative=len(negative),
        )
        return _OUTPUT_DIR

    async def _write_csv(self, entries: list[DatasetEntry]) -> None:
        """
        [ROLE] DatasetEntry 리스트를 CSV로 저장.
               논문 재현 실험에서 직접 로드 가능한 포맷.

        [DATA FLOW]
            list[DatasetEntry] ──▶ CSV rows ──▶ dataset.csv
        """
        path = _OUTPUT_DIR / "dataset.csv"
        fieldnames = [
            "entry_id", "timestamp_ns", "drone_id", "src_ip",
            "protocol", "msg_type", "attacker_level",
            "ttp_ids", "label", "confidence", "source", "stix_bundle_id",
        ]
        async with aiofiles.open(path, "w", encoding="utf-8", newline="") as f:
            # CSV 헤더
            await f.write(",".join(fieldnames) + "\n")
            for e in entries:
                row = [
                    e.entry_id,
                    str(e.timestamp_ns),
                    e.drone_id,
                    e.src_ip,
                    e.protocol.value,
                    e.msg_type,
                    e.attacker_level.name if e.attacker_level else "",
                    "|".join(e.ttp_ids),
                    str(e.label),
                    f"{e.confidence:.4f}",
                    e.source,
                    e.stix_bundle_id,
                ]
                await f.write(",".join(row) + "\n")
        logger.info("csv_written", path=str(path), rows=len(entries))

    async def _write_metadata(
        self,
        positive: list[DatasetEntry],
        negative: list[DatasetEntry],
    ) -> None:
        """
        [ROLE] 데이터셋 메타데이터를 YAML로 저장.
               논문 §6 Table IV 데이터 소스.

        [DATA FLOW]
            positive/negative 통계 ──▶ metadata.yaml
        """
        # TTP 커버리지 집계
        all_ttps: set[str] = set()
        level_dist: dict[str, int] = {}
        for e in positive:
            all_ttps.update(e.ttp_ids)
            lv = e.attacker_level.name if e.attacker_level else "unknown"
            level_dist[lv] = level_dist.get(lv, 0) + 1

        metadata = {
            "name"      : _DATASET_NAME,
            "version"   : _DATASET_VERSION,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "source"    : "Damn Vulnerable Drone honeypot deployment",
            "framework" : "MIRAGE-UAS (Moving-target Intelligent Responsive Agentic deception enGinE for UAS)",
            "paper"     : "ACM CCS 2026 (under review)",
            "drone_protocol": "MAVLink 2.0 / ArduPilot SITL",
            "attack_surfaces": ["MAVLink", "HTTP", "RTSP", "WebSocket"],
            "attacker_model" : "L0-L4 (MIRAGE-UAS §3 Threat Model)",
            "attck_version"  : "ATT&CK for ICS v14",
            "statistics": {
                "total_samples"  : len(positive) + len(negative),
                "positive_count" : len(positive),
                "negative_count" : len(negative),
                "class_ratio"    : f"1:{len(negative)/max(len(positive),1):.1f}",
                "unique_ttps"    : sorted(all_ttps),
                "ttp_count"      : len(all_ttps),
                "by_attacker_level": level_dist,
            },
            "schema": {
                "entry_id"       : "UUID v4",
                "timestamp_ns"   : "Unix timestamp (nanoseconds)",
                "drone_id"       : "Honey drone instance ID (honey_01..03)",
                "src_ip"         : "Attacker source IP",
                "protocol"       : "mavlink | http | rtsp | websocket",
                "msg_type"       : "MAVLink message type or HTTP method",
                "attacker_level" : "L0_SCRIPT_KIDDIE..L4_APT (blank=benign)",
                "ttp_ids"        : "Pipe-separated ATT&CK for ICS TTP IDs",
                "label"          : "0=benign, 1=attack",
                "confidence"     : "Classification confidence [0.0, 1.0]",
                "source"         : "honeydrone | synthetic | scenario_*",
                "stix_bundle_id" : "STIX 2.1 bundle reference",
            },
            "license" : "CC BY 4.0",
            "contact" : "kmseong0508@kyonggi.ac.kr",
        }

        path = _OUTPUT_DIR / "metadata.yaml"
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(yaml.dump(metadata, allow_unicode=True, sort_keys=False))
        logger.info("metadata_written", path=str(path))

    async def _write_readme(
        self,
        positive: list[DatasetEntry],
        negative: list[DatasetEntry],
    ) -> None:
        """[ROLE] 데이터셋 사용 설명서 README.md 생성."""
        total = len(positive) + len(negative)
        content = f"""# {_DATASET_NAME}-{_DATASET_VERSION}

**Source**: MIRAGE-UAS agentic honey drone deployment  
**Protocol**: MAVLink 2.0 / ArduPilot SITL (Damn Vulnerable Drone)  
**TTP Framework**: MITRE ATT&CK for ICS v14  
**Paper**: ACM CCS 2026 (under review)

## Statistics

| Category | Count |
|----------|-------|
| Total samples | {total} |
| Positive (attack) | {len(positive)} |
| Negative (benign) | {len(negative)} |

## Schema

See `metadata.yaml` for full field descriptions.

## Citation

```bibtex
@inproceedings{{mirage-uas-2026,
  title  = {{MIRAGE-UAS: MTD + Agentic Deception for UAS Security}},
  author = {{DS Lab, Kyonggi University}},
  booktitle = {{ACM CCS 2026}},
  year   = {{2026}},
}}
```

## License

Creative Commons Attribution 4.0 International (CC BY 4.0)
"""
        path = _OUTPUT_DIR / "README.md"
        async with aiofiles.open(path, "w", encoding="utf-8") as f:
            await f.write(content)

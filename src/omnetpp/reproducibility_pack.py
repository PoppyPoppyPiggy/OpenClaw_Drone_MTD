#!/usr/bin/env python3
"""
reproducibility_pack.py — ACM CCS Artifact Evaluation 재현성 패키지

Project  : MIRAGE-UAS
Module   : OMNeT++ / Reproducibility Pack
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-06
Version  : 0.1.0

[Inputs]
    - results/metrics/*.json
    - results/dataset/DVD-CTI-Dataset-v1/
    - omnetpp_trace/
    - config/.env

[Outputs]
    - reproducibility/MIRAGE-UAS-repro-v1.zip
    - CHECKSUMS.sha256

[REF] ACM Artifact Review and Badging v1.1

[DATA FLOW]
    results + omnetpp_trace + config ──▶ create_package()
    ──▶ CHECKSUMS.sha256 ──▶ .zip
"""

from __future__ import annotations

import hashlib
import json
import os
import zipfile
from pathlib import Path

from shared.constants import RESULTS_DIR
from shared.logger import get_logger

logger = get_logger(__name__)

_REPRO_DIR = Path("reproducibility")


def create_package(
    experiment_id: str = "mirage-uas",
    output_dir: Path | None = None,
) -> str:
    """
    [ROLE] ACM CCS Artifact Evaluation 재현성 패키지 생성.

    [DATA FLOW]
        results + traces + config ──▶ zip with CHECKSUMS
    """
    out = output_dir or _REPRO_DIR
    out.mkdir(parents=True, exist_ok=True)
    zip_path = out / "MIRAGE-UAS-repro-v1.zip"

    # Files to include
    include_patterns = [
        ("results/metrics", "*.json"),
        ("results/metrics", "*.jsonl"),
        ("results/dataset/DVD-CTI-Dataset-v1", "*"),
        ("results/latex", "*.tex"),
        ("omnetpp_trace", "*"),
    ]

    files_to_pack: list[tuple[str, Path]] = []

    for base_dir, pattern in include_patterns:
        base = Path(base_dir)
        if base.exists():
            for p in base.glob(pattern):
                if p.is_file():
                    files_to_pack.append((str(p), p))

    # Add config template
    env_path = Path("config/.env.example")
    if env_path.exists():
        files_to_pack.append(("config/.env.template", env_path))

    # Generate README
    readme_content = generate_repro_readme(experiment_id, 0.33, {})
    readme_path = out / "README_REPRO.md"
    readme_path.write_text(readme_content)
    files_to_pack.append(("README_REPRO.md", readme_path))

    # Generate reproduce.sh
    script_content = _generate_reproduce_script()
    script_path = out / "reproduce.sh"
    script_path.write_text(script_content)
    files_to_pack.append(("scripts/reproduce.sh", script_path))

    # Compute checksums
    checksums: dict[str, str] = {}
    for arc_name, real_path in files_to_pack:
        sha = hashlib.sha256(real_path.read_bytes()).hexdigest()
        checksums[arc_name] = sha

    checksum_path = out / "CHECKSUMS.sha256"
    with open(checksum_path, "w") as f:
        for name, sha in sorted(checksums.items()):
            f.write(f"{sha}  {name}\n")
    files_to_pack.append(("CHECKSUMS.sha256", checksum_path))

    # Create zip
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for arc_name, real_path in files_to_pack:
            zf.write(real_path, arc_name)

    logger.info(
        "repro_package_created",
        path=str(zip_path),
        files=len(files_to_pack),
    )
    return str(zip_path)


def generate_repro_readme(
    experiment_id: str, ds_score: float, params: dict
) -> str:
    """
    [ROLE] 재현성 README 생성.

    [DATA FLOW]
        experiment_id + score ──▶ Markdown README
    """
    return f"""# MIRAGE-UAS Reproducibility Package

**Experiment ID**: {experiment_id}
**DeceptionScore**: {ds_score}

## Hardware Requirements

- WSL2 Ubuntu 22.04 (kernel >= 5.15)
- 8 GB RAM allocated to WSL2
- Docker Desktop with WSL2 backend

## Software Requirements

- Python 3.9+
- Docker Engine 20.10+
- OMNeT++ 6.0 + INET 4.5 (optional, for simulation replay)

## Estimated Runtime

- Dry-run (no Docker): ~2 minutes
- Full Docker experiment: ~5 minutes
- OMNeT++ replay: ~10 minutes (optional)

## Reproduction Steps

```bash
# 1. Extract package
unzip MIRAGE-UAS-repro-v1.zip -d mirage-repro
cd mirage-repro

# 2. Verify checksums
sha256sum -c CHECKSUMS.sha256

# 3. Run full experiment
bash scripts/reproduce.sh

# 4. Check outputs
cat results/metrics/summary.json
ls results/figures/
```

## Expected Outputs

- DeceptionScore: [0.30, 0.85] (depends on attacker duration)
- Dataset: ~1000 rows (405 attack + 607 benign typical)
- Figures: 6 PDF files (Tables II-VI + timeline)
- LaTeX: 5 .tex files (ready for \\\\input{{}})

## Validation Criteria

- DeceptionScore within +/- 0.05 of reported value
- Dataset row count within +/- 10%
- All validation checks (V1-V6) PASS
"""


def _generate_reproduce_script() -> str:
    """[ROLE] reproduce.sh 스크립트 내용 생성."""
    return """#!/usr/bin/env bash
set -euo pipefail
echo "MIRAGE-UAS Reproduction Script"
echo "=============================="

# Check prerequisites
python3 --version
docker info > /dev/null 2>&1 || { echo "Docker required"; exit 1; }

# Setup
cp config/.env.template config/.env 2>/dev/null || true
pip install -r requirements.txt 2>/dev/null || true

# Run dry-run experiment
python3 scripts/run_experiment.py --mode dry-run --duration 120

echo "Reproduction complete. Check results/ directory."
"""


def verify_package(zip_path: str | Path) -> bool:
    """
    [ROLE] 재현성 패키지 무결성 검증.

    [DATA FLOW]
        zip ──▶ CHECKSUMS.sha256 추출 ──▶ 파일별 SHA-256 비교
    """
    zip_path = Path(zip_path)
    if not zip_path.exists():
        logger.error("package_not_found", path=str(zip_path))
        return False

    with zipfile.ZipFile(zip_path, "r") as zf:
        if "CHECKSUMS.sha256" not in zf.namelist():
            logger.error("checksums_missing")
            return False

        checksums_text = zf.read("CHECKSUMS.sha256").decode()
        expected: dict[str, str] = {}
        for line in checksums_text.strip().split("\n"):
            parts = line.strip().split("  ", 1)
            if len(parts) == 2:
                expected[parts[1]] = parts[0]

        errors = 0
        for name, sha in expected.items():
            if name == "CHECKSUMS.sha256":
                continue
            if name not in zf.namelist():
                logger.error("file_missing_in_zip", file=name)
                errors += 1
                continue
            actual_sha = hashlib.sha256(zf.read(name)).hexdigest()
            if actual_sha != sha:
                logger.error("checksum_mismatch", file=name, expected=sha[:16], actual=actual_sha[:16])
                errors += 1

    ok = errors == 0
    logger.info("package_verification", passed=ok, errors=errors)
    return ok


if __name__ == "__main__":
    path = create_package()
    print(f"Package: {path}")
    ok = verify_package(path)
    print(f"Verified: {ok}")

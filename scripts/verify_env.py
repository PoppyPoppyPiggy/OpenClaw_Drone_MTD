#!/usr/bin/env python3
"""
verify_env.py — WSL2 + Docker Desktop 환경 자동 검증

Project  : CTI-RL-MTD Honey Drone Testbed
Module   : Phase 0 / 환경 검증
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-02
Version  : 0.1.0

[Inputs]
    - 시스템 환경 (WSL2 커널, Docker daemon, Python 버전)
    - config/.env.example (파라미터 키 완전성 검사)

[Outputs]
    - 터미널 검증 리포트 (PASS / WARN / FAIL)
    - results/logs/env_check.json (CI 연동용)

[Dependencies]
    - subprocess (stdlib)
    - shutil     (stdlib)
    - platform   (stdlib)
"""

import json
import logging
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

import structlog

# ── Logger 설정 ───────────────────────────────────────────────────────────────
structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger()

# ── 상수 (인프라 고정값 — RL 탐색 제외) ──────────────────────────────────────
MIN_PYTHON_MAJOR = 3
MIN_PYTHON_MINOR = 11
MIN_MEMORY_GB = 8
MIN_CPU_COUNT = 4
REQUIRED_COMMANDS = ["docker", "docker-compose", "wsl.exe"]
REQUIRED_PORTS = [14551, 14552, 14553, 8765]   # 허니드론 + CTI API
ENV_EXAMPLE_PATH = Path(__file__).parent.parent / "config" / ".env.example"
RESULTS_DIR = Path(__file__).parent.parent / "results" / "logs"


# ── 데이터 모델 ───────────────────────────────────────────────────────────────
class CheckStatus(Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    message: str
    detail: str = ""

    def __repr__(self) -> str:
        icon = {"PASS": "✅", "WARN": "⚠️ ", "FAIL": "❌"}[self.status.value]
        return f"{icon} [{self.status.value}] {self.name}: {self.message}"


@dataclass
class EnvReport:
    results: list[CheckResult] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(r.status != CheckStatus.FAIL for r in self.results)

    def add(self, result: CheckResult) -> None:
        self.results.append(result)

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "summary": {s.value: sum(1 for r in self.results if r.status == s)
                        for s in CheckStatus},
            "checks": [
                {"name": r.name, "status": r.status.value,
                 "message": r.message, "detail": r.detail}
                for r in self.results
            ],
        }


# ── 검증 함수들 ───────────────────────────────────────────────────────────────

def check_python_version() -> CheckResult:
    # ── [ROLE] ──────────────────────────────────────────────────────────────
    # Python 버전이 3.11+ 인지 확인 (match-case, f-string 고급 기능 요구)
    # [DATA FLOW] sys.version_info ──▶ CheckResult
    # ────────────────────────────────────────────────────────────────────────
    major, minor = sys.version_info.major, sys.version_info.minor
    version_str = f"{major}.{minor}.{sys.version_info.micro}"

    if major >= MIN_PYTHON_MAJOR and minor >= MIN_PYTHON_MINOR:
        return CheckResult("Python Version", CheckStatus.PASS, version_str)
    return CheckResult(
        "Python Version", CheckStatus.FAIL,
        f"{version_str} (required ≥ {MIN_PYTHON_MAJOR}.{MIN_PYTHON_MINOR})"
    )


def check_wsl2() -> CheckResult:
    # ── [ROLE] ──────────────────────────────────────────────────────────────
    # WSL2 커널 동작 여부 확인 (WSL1과 네트워크 동작 방식이 다름)
    # [DATA FLOW] /proc/version 파일 ──▶ 커널 문자열 파싱 ──▶ CheckResult
    # ────────────────────────────────────────────────────────────────────────
    proc_version = Path("/proc/version")
    if not proc_version.exists():
        return CheckResult("WSL2", CheckStatus.WARN,
                           "Not running in WSL2 (Linux /proc/version 없음)")

    content = proc_version.read_text()
    if "microsoft" in content.lower() and "wsl2" in content.lower():
        kernel = content.split()[2] if len(content.split()) > 2 else "unknown"
        return CheckResult("WSL2", CheckStatus.PASS, f"WSL2 커널 확인 ({kernel})")
    if "microsoft" in content.lower():
        return CheckResult("WSL2", CheckStatus.WARN,
                           "WSL1 감지됨 — networkingMode=mirrored 미지원")
    return CheckResult("WSL2", CheckStatus.WARN,
                       "WSL2 환경 아님 — 네트워크 설정 수동 확인 필요")


def check_available_memory() -> CheckResult:
    # ── [ROLE] ──────────────────────────────────────────────────────────────
    # 가용 RAM이 SITL 멀티 인스턴스 실행에 충분한지 확인
    # [DATA FLOW] /proc/meminfo ──▶ MemTotal 파싱 ──▶ GB 변환 ──▶ CheckResult
    # ────────────────────────────────────────────────────────────────────────
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return CheckResult("Memory", CheckStatus.WARN, "/proc/meminfo 없음")

    for line in meminfo.read_text().splitlines():
        if line.startswith("MemTotal:"):
            kb = int(line.split()[1])
            gb = kb / (1024 ** 2)
            status = CheckStatus.PASS if gb >= MIN_MEMORY_GB else CheckStatus.WARN
            msg = f"{gb:.1f} GB (권장 ≥ {MIN_MEMORY_GB} GB)"
            return CheckResult("Available Memory", status, msg)

    return CheckResult("Memory", CheckStatus.WARN, "메모리 정보 파싱 실패")


def check_cpu_count() -> CheckResult:
    # ── [ROLE] ──────────────────────────────────────────────────────────────
    # 가용 CPU 코어 수 확인 (SITL 병렬 실행 최소 4코어 권장)
    # [DATA FLOW] os.cpu_count() ──▶ CheckResult
    # ────────────────────────────────────────────────────────────────────────
    count = os.cpu_count() or 0
    status = CheckStatus.PASS if count >= MIN_CPU_COUNT else CheckStatus.WARN
    return CheckResult(
        "CPU Cores", status,
        f"{count} 코어 (권장 ≥ {MIN_CPU_COUNT})"
    )


def check_command_available(cmd: str) -> CheckResult:
    # ── [ROLE] ──────────────────────────────────────────────────────────────
    # 필수 외부 명령어(docker, wsl.exe 등)의 PATH 존재 여부 확인
    # [DATA FLOW] shutil.which(cmd) ──▶ 경로 존재 여부 ──▶ CheckResult
    # ────────────────────────────────────────────────────────────────────────
    path = shutil.which(cmd)
    if path:
        return CheckResult(f"Command: {cmd}", CheckStatus.PASS, path)
    return CheckResult(f"Command: {cmd}", CheckStatus.FAIL,
                       f"'{cmd}' not found in PATH")


def check_docker_daemon() -> CheckResult:
    # ── [ROLE] ──────────────────────────────────────────────────────────────
    # Docker daemon 실행 상태 및 버전 확인
    # [DATA FLOW] docker info (subprocess) ──▶ 파싱 ──▶ CheckResult
    # ────────────────────────────────────────────────────────────────────────
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            version = result.stdout.strip()
            return CheckResult("Docker Daemon", CheckStatus.PASS,
                               f"실행 중 (v{version})")
        return CheckResult("Docker Daemon", CheckStatus.FAIL,
                           "Docker daemon 응답 없음 — Docker Desktop 실행 확인")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return CheckResult("Docker Daemon", CheckStatus.FAIL, str(e))


def check_docker_network_isolation() -> CheckResult:
    # ── [ROLE] ──────────────────────────────────────────────────────────────
    # honey_isolated 네트워크 사전 생성 여부 확인
    # (없으면 docker-compose up 시 자동 생성 — WARN 수준)
    # [DATA FLOW] docker network ls (subprocess) ──▶ 네트워크명 검색 ──▶ CheckResult
    # ────────────────────────────────────────────────────────────────────────
    try:
        result = subprocess.run(
            ["docker", "network", "ls", "--format", "{{.Name}}"],
            capture_output=True, text=True, timeout=10
        )
        networks = result.stdout.splitlines()
        if "honey_isolated" in networks:
            return CheckResult("Docker Network", CheckStatus.PASS,
                               "honey_isolated 네트워크 존재")
        return CheckResult("Docker Network", CheckStatus.WARN,
                           "honey_isolated 없음 — docker-compose up 시 자동 생성됨")
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        return CheckResult("Docker Network", CheckStatus.WARN, str(e))


def check_port_available(port: int) -> CheckResult:
    # ── [ROLE] ──────────────────────────────────────────────────────────────
    # 지정 포트가 이미 사용 중인지 확인 (허니드론 포트 충돌 사전 차단)
    # [DATA FLOW] ss -tulnp (subprocess) ──▶ 포트 번호 검색 ──▶ CheckResult
    # ────────────────────────────────────────────────────────────────────────
    try:
        result = subprocess.run(
            ["ss", "-tulnp"],
            capture_output=True, text=True, timeout=5
        )
        if f":{port}" in result.stdout:
            return CheckResult(f"Port {port}", CheckStatus.WARN,
                               f"포트 {port} 사용 중 — 충돌 가능")
        return CheckResult(f"Port {port}", CheckStatus.PASS, "사용 가능")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        # ss 명령 없는 환경 (비-Linux) — 건너뜀
        return CheckResult(f"Port {port}", CheckStatus.WARN,
                           "포트 확인 불가 (ss 명령 없음)")


def check_env_example_keys() -> CheckResult:
    # ── [ROLE] ──────────────────────────────────────────────────────────────
    # .env.example 파일의 모든 키가 현재 .env에 존재하는지 대조
    # 빠진 키 = RL 옵티마이저 실행 전에 반드시 채워야 함
    # [DATA FLOW] .env.example 파싱 ──▶ .env 키 대조 ──▶ 누락 키 목록 ──▶ CheckResult
    # ────────────────────────────────────────────────────────────────────────
    if not ENV_EXAMPLE_PATH.exists():
        return CheckResult(".env.example", CheckStatus.WARN,
                           f"{ENV_EXAMPLE_PATH} 없음")

    example_keys: set[str] = set()
    for line in ENV_EXAMPLE_PATH.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            example_keys.add(line.split("=")[0])

    env_path = ENV_EXAMPLE_PATH.parent / ".env"
    if not env_path.exists():
        return CheckResult(".env", CheckStatus.WARN,
                           ".env 파일 없음 — cp config/.env.example config/.env 실행 후 값 입력")

    env_keys: set[str] = set()
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            env_keys.add(line.split("=")[0])

    missing = example_keys - env_keys
    if missing:
        return CheckResult(".env Keys", CheckStatus.WARN,
                           f"누락 키 {len(missing)}개",
                           detail=", ".join(sorted(missing)))
    return CheckResult(".env Keys", CheckStatus.PASS,
                       f"모든 키 존재 ({len(example_keys)}개)")


# ── 메인 실행 ─────────────────────────────────────────────────────────────────

def run_all_checks() -> EnvReport:
    # ── [ROLE] ──────────────────────────────────────────────────────────────
    # 모든 검증 항목을 순서대로 실행하고 EnvReport에 집계
    # [DATA FLOW] check 함수들 ──▶ EnvReport ──▶ JSON 저장 + 터미널 출력
    # ────────────────────────────────────────────────────────────────────────
    report = EnvReport()

    checks: list[Callable[[], CheckResult]] = [
        check_python_version,
        check_wsl2,
        check_available_memory,
        check_cpu_count,
        *[lambda c=cmd: check_command_available(c) for cmd in REQUIRED_COMMANDS],
        check_docker_daemon,
        check_docker_network_isolation,
        *[lambda p=port: check_port_available(p) for p in REQUIRED_PORTS],
        check_env_example_keys,
    ]

    for check_fn in checks:
        result = check_fn()
        report.add(result)
        print(repr(result))
        if result.detail:
            print(f"   └─ {result.detail}")

    return report


def save_report(report: EnvReport) -> None:
    # ── [ROLE] ──────────────────────────────────────────────────────────────
    # EnvReport를 JSON으로 직렬화하여 results/logs/에 저장 (CI 연동용)
    # [DATA FLOW] EnvReport ──▶ dict ──▶ JSON 파일 (results/logs/env_check.json)
    # ────────────────────────────────────────────────────────────────────────
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    output_path = RESULTS_DIR / "env_check.json"
    output_path.write_text(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
    logger.info("env_check saved", path=str(output_path))


if __name__ == "__main__":
    print("\n" + "═" * 60)
    print("  CTI-RL-MTD Honey Drone — Phase 0: 환경 검증")
    print("═" * 60 + "\n")

    report = run_all_checks()
    save_report(report)

    print("\n" + "─" * 60)
    summary = report.to_dict()["summary"]
    print(f"  결과: PASS={summary['PASS']}  WARN={summary['WARN']}  FAIL={summary['FAIL']}")
    print("─" * 60)

    if not report.passed:
        print("\n❌ FAIL 항목 해결 후 Phase 1 진행하세요.\n")
        sys.exit(1)

    print("\n✅ 환경 검증 완료 — Phase 1 진행 가능\n")
    sys.exit(0)

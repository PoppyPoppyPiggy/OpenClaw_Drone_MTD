#!/usr/bin/env python3
"""
logger.py — MIRAGE-UAS structlog 설정

Project  : MIRAGE-UAS
Module   : Shared / Logger
Author   : DS Lab / 민성 <kmseong0508@kyonggi.ac.kr>
Created  : 2026-04-03
Version  : 0.1.0

[Inputs]
    - LOG_LEVEL, LOG_FORMAT (constants.py)

[Outputs]
    - structlog.get_logger() 반환 — 모든 모듈에서 사용
    - JSON 포맷: 논문 재현성을 위한 구조화 로그

[사용법]
    from shared.logger import get_logger
    logger = get_logger(__name__)
    logger.info("event captured", drone_id="honey_01", msg_type="COMMAND_LONG")
"""

import logging
import os
import sys

import structlog


def _configure_structlog(log_level: str = "INFO", log_format: str = "json") -> None:
    """
    [ROLE] structlog 전역 설정 초기화.
           JSON 포맷(논문 실험 로그)과 콘솔 포맷(개발용)을 지원.

    [DATA FLOW]
        log_level, log_format ──▶ structlog.configure() ──▶ 전역 적용
    """
    log_level_int = getattr(logging, log_level.upper(), logging.INFO)

    # stdlib 루트 로거 설정
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level_int,
    )

    shared_processors = [
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if log_format.lower() == "json":
        # 논문 실험 로그: JSON Lines 포맷 (jq로 분석 가능)
        renderer = structlog.processors.JSONRenderer()
    else:
        # 개발용: 컬러 콘솔 출력
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            *shared_processors,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.ExceptionRenderer(),
            renderer,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


# 모듈 임포트 시 자동 설정
_log_level  = os.environ.get("LOG_LEVEL",  "INFO")
_log_format = os.environ.get("LOG_FORMAT", "json")
_configure_structlog(_log_level, _log_format)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """
    [ROLE] 모듈별 logger 인스턴스 반환.

    [DATA FLOW]
        name (모듈명) ──▶ structlog.get_logger(name) ──▶ BoundLogger
    """
    return structlog.get_logger(name)

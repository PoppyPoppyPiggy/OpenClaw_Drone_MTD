# MIRAGE-UAS

**Moving-target Intelligent Responsive Agentic deception enGinE for UAS**

> ACM CCS 2026 Cycle B 제출 대상 논문 테스트베드  
> DS Lab, Kyonggi University | 과제번호 915024201 (DAPA)

---

## 프로젝트 구조

```
mirage-uas/
├── config/
│   ├── .env.example              # 수식 파라미터 템플릿 (값 직접 입력)
│   ├── .wslconfig                # WSL2 메모리/CPU 설정 → C:\Users\<이름>\.wslconfig
│   └── docker-compose.honey.yml # DVD 허니드론 3개 스택
│
├── src/
│   ├── shared/                   # 공유 데이터 모델 + 상수 + 로거
│   ├── honey_drone/              # Track A: Agentic Decoy Engine (OpenClaw 역전용)
│   ├── mtd/                      # Track A: MTD Surface Controller
│   ├── cti_pipeline/             # Track B: 캡처 → 파서 → STIX 2.1 → API
│   ├── dataset/                  # Track B: 양성/음성 수집 → DVD-CTI-Dataset-v1
│   └── evaluation/               # 논문 Table II/III/IV/V 메트릭
│
├── scripts/
│   ├── verify_env.py             # Phase 0: WSL2 + Docker 환경 검증
│   ├── verify_ports.py           # Phase 0: MAVLink 포트 검증
│   └── run_experiment.py         # 실험 진입점
│
├── tests/integration/
│   └── test_e2e_mirage.py        # E2E-01~08 통합 테스트
│
└── requirements.txt
```

---

## 빠른 시작

```bash
# 1. 환경변수 설정
cp config/.env.example config/.env
# config/.env 열어서 수식 파라미터 값 입력

# 2. 패키지 설치
pip install -r requirements.txt

# 3. 환경 검증
python scripts/verify_env.py

# 4. 실험 실행 (dry-run — Docker 없이 파이프라인 테스트)
python scripts/run_experiment.py --mode dry-run --duration 120

# 5. E2E 테스트
pytest tests/integration/test_e2e_mirage.py -v
```

---

## 논문 기여 (ACM CCS 2026)

| 기여 | 내용 |
|------|------|
| C1 | MTD + Agentic Decoy 최초 통합 (D3GF 대비 실구현) |
| C2 | Honeypot 기반 레이블 UAS CTI 데이터셋 최초 공개 |
| C3 | OpenClaw-inspired Agentic Honeydrone 설계 패턴 |

---

## 관련 논문

- TIFS T-IFS-25285-2026 (기존 CTI-RL-MTD)  
- D3GF: Seo et al., IEEE Access 2023  
- HoneyGPT: Wang et al., 2024  
- Mirra et al., arXiv 2026

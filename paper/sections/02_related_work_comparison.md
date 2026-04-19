# MIRAGE-UAS — Related Work Comparison Table

**Scope**: ACM CCS / USENIX Security / NDSS / IEEE S&P and allied top-tier
venues; 5-year window (2021–2025); MDPI journals excluded per filter.
Each row answers two questions: *what did the prior work achieve?* and
*how does MIRAGE-UAS do it better or differently?*

---

## Table I — Comparison with top-tier prior work

| # | Paper (venue, year) | What they achieved | MIRAGE-UAS differentiation |
|---|---|---|---|
| 1 | **Ayzenshteyn et al.** — *Cloak, Honey, Trap: Proactive Defenses Against LLM Agents* — **USENIX Security 2025** | First proactive-defense taxonomy for LLM-driven attackers. Three primitives (Cloak = entropy injection; Honey = LLM-specific honeytokens; Trap = prompt-injection that stalls the agent). Demonstrates resource-depletion and early-disclosure against autonomous LLM agents on SSH-like surfaces. | Cloak/Honey/Trap operates **only at the interaction (Tier 3) layer**. MIRAGE-UAS adopts the same "LLM-vs-LLM" adversary model but adds (a) a Tier 2 tactical LLM that *adapts* the lure per packet and (b) a Tier 1 strategic LLM that *coordinates three honeydrones*. Our 3×3 matrix (Table IX) supplements their binary outcome (agent trapped / not) with a continuous **packet-level belief trajectory** (AUC ∈ [0, 1]). |
| 2 | **Guan et al.** — *Cyber-Physical Deception Through Coordinated IoT Honeypots (CPDS)* — **USENIX Security 2025** | Coordinates heterogeneous IoT honeypots (camera, router, PLC) to maintain cross-layer consistency; uses SDN to reshape traffic and dependency rules so that the faked CPS topology remains internally coherent under attacker probing. | CPDS coordinates **at the network layer** via SDN. MIRAGE-UAS coordinates **at the decision layer** — a Tier 1 LLM emits typed *strategic directives* that Tier 2 tactical LLMs consume as prompt context rather than as traffic rewrites. Our coordination operates over a single UDP control channel (19995) and is observable in 60 directives / 10-min live run with 100 % delivery. |
| 3 | **Schiller et al.** — *Drone Security and the Mysterious Case of DJI's DroneID* — **NDSS 2023** | Reverse-engineers DJI's DroneID PHY-layer protocol, decodes position broadcasts with COTS SDRs, fuzzes firmware, and discloses 16 CVEs across two DJI models. Demonstrates absence of expected encryption on live commercial drones. | DroneID work targets **vendor-specific RF telemetry confidentiality**. MIRAGE-UAS addresses the orthogonal problem of *live attacker engagement* when the adversary is already inside the MAVLink session. Our Tier 3 lure emulates the OpenClaw SDK wire format so an attacker who has bypassed link-layer protections still lands in a decoy; our HTUR tracker quantifies how many planted tokens they re-use (live: HTUR 1.00 on 3-drone fleet). |
| 4 | **Zhang et al.** — *Collaborative Honeypot Defense in UAV Networks: A Learning-Based Game Approach* — **IEEE TIFS 2023** | Markov game between UAV fleet and adversary; alternating best-response produces a coordinated honeypot-deployment policy. Demonstrates measurable deception gain vs. static allocation in simulated UAV networks. | TIFS work operates on a **discrete action space solved by RL** (no language model, no real protocol). MIRAGE-UAS keeps the fictitious-play structure (Game-EQ baseline with Nash-convergence plot) but *replaces* the defender agent with an LLM tool-use policy, *adds* a strategic LLM on top, and *measures* the impact under MAVLink-fidelity Docker deployment. Our Cramér's V = 0.32–0.45 (large) across 9 runs empirically verifies phase-aware behaviour that their scalar-Q representation cannot expose. |
| 5 | **Veksler, Akkaya, Uluagac** — *Catch Me If You Can: Covert Information Leakage from Drones Using MAVLink* — **ACM AsiaCCS 2024** | Identifies covert channels in MAVLink (default-broadcast messages, redundant fields) and shows high-throughput data exfiltration to an external receiver, even in the presence of an active warden at the GCS. | AsiaCCS paper is an **attack-side contribution** exposing MAVLink weaknesses. MIRAGE-UAS is the **matching defence** — our Tier 2 `openclaw_agent.py` instruments the same protocol but under defender control, and Tier 3 can inject plausible noise on the very default-broadcast messages they exploit. We cite this paper as the canonical motivation for our MAVLink v2 ardupilotmega implementation and for the "broadcast-surface" risk listed in §5 Limitations. |
| 6 | **Li et al.** — *WIP: Hijacking Attacks on UAV Follow-Me Systems* — **USENIX VehicleSec 2025** | Raises UAV follow-me attack success from 47 % → 95 % by leveraging sensor inaccuracies and gimbal instability. Demonstrates attacker-side use of CV model quirks. | Follow-me attack is **visual/physical layer**. MIRAGE-UAS is complementary — it does not address the CV channel but addresses the command-plane (MAVLink/HTTP/WS) that an attacker uses to pivot *after* a follow-me hijack. Cited as motivation for an end-to-end defence stack (physical + command-plane). |
| 7 | **Erba et al.** — *ConfuSense: Sensor Reconfiguration Attacks for Stealthy UAV Manipulation* — **USENIX VehicleSec 2025** | Stealth sensor-reconfig attack crashes or deviates a UAV by ≥ 30 m without triggering legacy IDS. Demonstrates the limits of standard detection under process-control semantics. | ConfuSense targets **onboard sensor pipelines**. MIRAGE-UAS targets **exposed network surfaces**. We acknowledge that a full UAS defence must combine both; our Tier 2 belief state `evasion_signals` can be extended to ingest sensor-plane anomalies (§6 Future Work hook). |
| 8 | **Pawlick, Colbert, Zhu** — *A Game-theoretic Taxonomy and Survey of Defensive Deception for Cybersecurity and Privacy* — **ACM Computing Surveys 2019** | Six-species taxonomy (perturbation, MTD, obfuscation, mixing, honey-x, attacker-engagement) + Stackelberg/Nash/Signalling formulations. Foundational survey. | We explicitly **anchor MIRAGE-UAS at the honey-x / attacker-engagement boundary** of this taxonomy (§2.5) and instantiate all three solution types: Signalling game (our QRE policy), Stackelberg (Game-EQ reference), and Nash (fictitious-play convergence). Our 7-metric game-theoretic battery (Table VIII) is the first concrete instantiation of this taxonomy on a live Docker UAV testbed. |
| 9 | **Song et al.** — *HoneyGPT: Breaking the Trilemma in Honeypots with LLMs* — **arXiv:2406.01882, 2024** (tracked at USENIX track) | First LLM-driven shell honeypot. Outperforms Cowrie + real OS on attack-success and temptation over three months of live Internet exposure. Chain-of-thought memory pruning handles long sessions. | HoneyGPT is **single-host SSH**. MIRAGE-UAS is **UAS-fleet MAVLink/HTTP/WS + RTSP + SSH** with three coordinated LLM layers. We also *replicate and fix* their implicit mode-collapse (their Section 4 reports "response repetition" but does not quantify it); our V1→V2 ablation (Table VII) shows mode collapse and mitigation across 9 seed-runs with χ² p = 0. |
| 10 | **Sladić et al. (HoneyLLM-related)** — *LLM Honeypot: Leveraging Large Language Models as Advanced Interactive Honeypot Systems* — **arXiv:2409.08234, 2024** | LLM-powered interactive honeypot, prompt-injection defenses, operational-latency study (0.72 s mean, 83.3 % detection precision). | Their detection precision is measured against **a fixed scripted attacker**. We pair three LLM defenders against three independent LLM belief trackers in a 3 × 3 matrix, exposing a cross-family structure their single-model evaluation cannot detect. |
| 11 | **Otal & Canbaz (LLM Agent Honeypot)** — *Monitoring AI Hacking Agents in the Wild* — **arXiv:2410.13919, 2024** | Augments Cowrie with prompt-injection banners + timing analysis to distinguish LLM agents from humans. Reports ~86 % precision in field deployment. | Monitors *presence* of LLM agents. We measure **deception lifetime** against LLM agents (packets-to-disbelief, AUC), a finer-grained question. Our belief trajectory (Figure fig_belief_trajectories_grid.png) decomposes *why* each agent eventually disbelieves (top suspicion signals). |
| 12 | **Bridges et al.** — *SoK: Honeypots & LLMs, More Than the Sum of Their Parts?* — **arXiv:2510.25939, 2025** | Systematisation of 30+ LLM-honeypot works; canonical architecture, attacker trichotomy (novice / scripted / LLM-driven), evaluation framework. | SoK identifies a methodological gap: "no prior work measures the attacker-side belief trajectory for LLM-honeypot pairings." MIRAGE-UAS fills precisely this gap for the UAS domain (§5.3 Table IX). We also contribute the DeceptionScore v2 composite metric that maps onto their proposed evaluation axes. |
| 13 | **Gutierrez et al.** — *Honeypot-Based Cyber Deception Against Malicious Reconnaissance via Hypergame Theory* — **Conference preprint (ResearchGate), 2022** | Hypergame formulation of defender–attacker perception gap. Proves bounds on deception stability under attacker strategy revision. | Theoretical. We operationalise **hypergame stability as a measurable metric** over our live belief-tracker matrix: Stability = misbelief_ratio × survival, reported per-cell in Table VIII. |
| 14 | **Horák et al.** — *Optimizing Honeypot Strategies Against Dynamic Lateral Movement Using POSGs* — **Computers & Security (Elsevier) 2019** | POSG formulation with defender full-observability, attacker partial-observability; dynamic honeypot allocation via lateral-movement graph. | We adopt the same POSG framing and **apply it to MAVLink-fidelity UAV state** rather than abstract graph edges. Our observation model uses `response_quality`, `timing_consistency`, and `service_footprint` as attacker proxies — concrete, measurable, and LLM-parseable. |

---

## Table II — Methodological scope comparison (top-tier only)

| Criterion | Cloak-Honey-Trap (USENIX'25) | CPDS (USENIX'25) | UAV Follow-Me (VehicleSec'25) | Zhang TIFS'23 | HoneyGPT (arXiv'24) | SoK LLM-Honeypot (arXiv'25) | **MIRAGE-UAS** |
|---|---|---|---|---|---|---|---|
| Domain = UAV/MAVLink | ✗ | ✗ (IoT) | ✓ (CV layer) | ✓ | ✗ (SSH) | ✗ | **✓ (full stack)** |
| Defender uses LLM | ✗ | ✗ | ✗ | ✗ | ✓ | survey | **✓ (3 tiers)** |
| Attacker modelled as LLM | ✓ (trap target) | ✗ | ✗ | ✗ | ✗ | survey | **✓ (belief tracker)** |
| Cross-organisation LLM comparison | ✗ | — | — | — | single-model | — | **✓ (3 orgs × 3 seeds)** |
| Strategic ↔ tactical coordination | ✗ | SDN only | ✗ | fleet game | ✗ | — | **✓ (Tier 1 ↔ Tier 2)** |
| Docker-reproducible testbed | partial | ✓ | field-only | sim-only | single image | — | **✓ (compose overlay)** |
| Packet-level deception lifetime | ✗ | ✗ | — | ✗ | session-level | identifies gap | **✓ (AUC / disbelief@pkt)** |
| Statistical rigour (bootstrap CI, χ², Cramér V) | single-run | single-run | A/B | sim CI | — | — | **✓ (3-seed × 1000 bootstrap)** |
| Honey-token uptake tracking | ✓ (conceptual) | ✗ | ✗ | ✗ | ✗ | ✗ | **✓ (HTUR/CPR/FSR live)** |
| Game-theoretic evaluation | prompt-injection lemma | consistency proof | — | Nash/BR | — | survey | **✓ (7-metric POSG)** |

**Legend**: ✓ = provided; ✗ = absent; — = not applicable; *partial* = claimed but not released.

---

## Table III — Verification links (all URLs live-checked during literature review)

| # | Paper | Link | Venue | Year |
|---|---|---|---|---|
| 1 | Cloak, Honey, Trap — Ayzenshteyn et al. | https://www.usenix.org/system/files/usenixsecurity25-ayzenshteyn.pdf | USENIX Security | 2025 |
| 2 | Cyber-Physical Deception (CPDS) — Guan et al. | https://www.usenix.org/system/files/usenixsecurity25-guan.pdf | USENIX Security | 2025 |
| 3 | Drone Security & DJI DroneID — Schiller et al. | https://www.ndss-symposium.org/wp-content/uploads/2023/02/ndss2023_f217_paper.pdf | NDSS | 2023 |
| 4 | UAV Collaborative Honeypot Game — Zhang et al. | https://dl.acm.org/doi/abs/10.1109/TIFS.2023.3318942 | IEEE TIFS | 2023 |
| 5 | Catch Me If You Can (MAVLink covert) — Veksler et al. | https://dl.acm.org/doi/10.1145/3634737.3637672 | ACM AsiaCCS | 2024 |
| 6 | UAV Follow-Me Hijacking — Li et al. | https://www.usenix.org/system/files/vehiclesec25-li-jiarui.pdf | USENIX VehicleSec | 2025 |
| 7 | ConfuSense Sensor Attack — Erba et al. | https://www.usenix.org/system/files/vehiclesec25-erba.pdf | USENIX VehicleSec | 2025 |
| 8 | Game-Theoretic Taxonomy — Pawlick, Colbert, Zhu | https://dl.acm.org/doi/10.1145/3337772 | ACM CSUR | 2019 |
| 9 | HoneyGPT — Song et al. | https://arxiv.org/html/2406.01882v2 | arXiv (USENIX track) | 2024 |
| 10 | LLM Honeypot — Otal, Sladić et al. | https://arxiv.org/html/2409.08234v1 | arXiv | 2024 |
| 11 | LLM Agent Honeypot — Reworr et al. | https://arxiv.org/html/2410.13919v2 | arXiv | 2024 |
| 12 | SoK: Honeypots & LLMs — Bridges et al. | https://arxiv.org/pdf/2510.25939 | arXiv (SoK) | 2025 |
| 13 | Hypergame Honeypots — Gutierrez et al. | https://www.researchgate.net/publication/367061524_Honeypot-Based_Cyber_Deception_Against_Malicious_Reconnaissance_via_Hypergame_Theory | Preprint | 2022 |
| 14 | POSG Honeypot Placement — Horák et al. | https://www.sciencedirect.com/science/article/abs/pii/S0167404819300665 | Computers & Security | 2019 |
| 15 | HoneyLLM (LNCS) — Xi'an Jiaotong-Liverpool | https://link.springer.com/chapter/10.1007/978-981-97-8801-9_13 | Springer LNCS (ICICS) | 2024 |
| 16 | Awesome ML-SP Papers (top-4 index) | https://github.com/gnipping/Awesome-ML-SP-Papers | GitHub curated list | 2024– |

MDPI papers excluded per requirement.

---

## Table IV — Where MIRAGE-UAS uniquely contributes

| Contribution | First appearance in security literature | Evidence in this paper |
|---|---|---|
| **3-tier hierarchical LLM deception on a live UAV testbed** | No prior work (closest: arXiv:2603.17272 is 2-tier, non-UAV, non-LLM-defender-fleet) | Docker stack § 3 + 10-min live run § 4 |
| **Cross-organisation LLM defender benchmark** | No prior work (HoneyGPT, HoneyLLM, LLM-Agent-Honeypot each use a single model family) | 3 models × 3 seeds × 50 ep, Table VII with bootstrap CI |
| **Mode-collapse mitigation with quantitative ablation** | HoneyGPT hints ("response repetition") but does not report entropy/χ². First rigorous report. | V1 0.69 bits → V2 2.10 bits, Cramér V 0.32–0.45, χ² p = 0 § 4.3 |
| **Packet-level LLM-vs-LLM deception lifetime matrix** | No prior work (SoK 2025 explicitly identifies this as an open gap). | 3 × 3 matrix with AUC, packets-to-disbelief, top-signal extraction § 5.3 |
| **Same-family mirror blind spot** (cross-model finding) | Novel | +27 % AUC on diagonal vs off-diagonal cells § 5.3 |
| **Defender-robustness inversion** (smaller LLM more robust) | Novel | Llama 8B σ = 0.014 vs Qwen 14B σ = 0.273 § 5.3 |
| **HTUR / CPR / FSR honey-token uptake tracking** | New metric family defined in this paper (aligns with SoK 2025 evaluation-axis proposal) | Offline ceiling (1.00) + live Docker (3-drone fleet) § 5.4 |
| **Docker-releasable research artefact with 3 tiers + 3 LLMs + 9-cell dataset** | No prior work at this scope | `config/docker-compose.honey.yml` + `.llm.yml`, `results/diagnostics/llm_vs_llm/*.json` |

---

## 집필 가이드 — 이 comparison을 §2에 통합하는 방법

1. **§2.1–§2.7 본문**은 `paper/sections/01_abstract_intro_related.md`의 기존
   서술을 유지.
2. **Table I** (위 행렬)을 §2.7 직전에 삽입. 각 cell의 오른쪽 열 ("MIRAGE
   differentiation") 텍스트를 본문 §2.1–§2.6 해당 위치에 **중복 없이 짧게
   인용**.
3. **Table II**는 paper의 supplementary material로 밀거나, §2.7 말미에
   축약판 (5 행 × 5 열) 형태로 포함.
4. **Table IV**는 §1.4 Contributions 직후 또는 §3.1 System Overview 앞에
   "What is new" 박스로 삽입.
5. CCS 리뷰어의 "prior work overlap" 공격 시 **Table II의 ✗ 연속 행**을
   답변 근거로 제시.

---

## 참고 — 포함 결정 기준 (기록용)

| 포함 | 이유 |
|---|---|
| USENIX Security 2025 × 2 | 최신 top-4, 정확한 domain (LLM honeypot / IoT deception) |
| USENIX VehicleSec 2025 × 2 | UAV 도메인 최신, 공격자 측 상보 |
| NDSS 2023 × 1 | UAV vendor-specific RF, 저희와 직교 (공격/방어 축) |
| IEEE TIFS 2023 × 1 | UAV + game-theoretic, 가장 가까운 peer (비-LLM) |
| ACM AsiaCCS 2024 × 1 | MAVLink covert channel — 방어 대상 공격 모델 인용 |
| ACM CSUR 2019 × 1 | 5-yr rule 초과이나 **foundational taxonomy** — 포지셔닝에 필수, 본문에서 "survey" 로 명시 |
| Elsevier COSE 2019 × 1 | POSG 형식화 — 본 논문 §4 formalism 직접 출처, 5-yr rule 초과지만 인용 필수 |
| arXiv 2024–2025 × 5 | 동료 peer-reviewed publication 도래 전이지만 공개 benchmark 수준, SoK 포함 | 

| 제외 | 이유 |
|---|---|
| MDPI journals (Computers, Sensors, Electronics 등) | 사용자 요청 |
| 5-yr 초과 (Carroll-Grosu 2011, McKelvey-Palfrey 1995, Crawford-Sobel 1982) | 본문 인용만 유지, 비교표 제외 |
| ICICS 2024 HoneyLLM | Springer LNCS — CCS-관련 level 아님, 비교표 마지막에만 |
| Mini-workshops (ARES, MTD workshop) | 사용자 filter에 맞지 않음 |

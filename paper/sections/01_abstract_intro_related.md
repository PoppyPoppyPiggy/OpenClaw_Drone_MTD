# MIRAGE-UAS: Hierarchical LLM-Driven Deception for UAV Honeydrone Fleets

*A Three-Tier Architecture with Packet-Level Cross-Model Evaluation*

---

## Abstract

Unmanned Aerial Systems (UAS) are increasingly targeted by multi-stage
adversaries that combine network reconnaissance, MAVLink-protocol abuse,
and credential-harvesting lateral movement. While prior LLM-honeypot
systems (HoneyGPT, HoneyLLM, LLMHoney) demonstrate that large language
models can animate single-host deception services, and a recent
hierarchical RL-LLM framework targets generic networks, the intersection
of **UAV-fleet deception, MAVLink-protocol fidelity, and
operator-gateway coordination** remains unexplored. Existing honeydrone
testbeds — from the seminal HoneyDrone of Daubert et al. (NOMS 2018) to
modern DRL-optimised variants (ACM TOIT 2024) — rely on static decoys
or narrow rule-based responders that decohere under adaptive attackers
and expose themselves within seconds once engaged.

We present **MIRAGE-UAS**, a hierarchical UAS deception framework whose
strategic, tactical, and interaction-level decisions are coordinated
through a three-tier Docker-deployed architecture. Tier 1 is an
OpenClaw-inspired *Ground Control Station* strategic agent that issues
typed directives over a dedicated UDP control channel; Tier 2 is a
per-honeydrone tactical LLM defender driven by a locally-served 8-14 B
model (Ollama); Tier 3 is an OpenClaw-SDK emulator that plants
UUID-tagged honey-tokens and tracks their re-use. The three layers run
at patrol (1 s), tactical (8 s), and strategic (30 s) cadences.

Over **nine runs** (3 cross-organisation LLMs — Llama 3.1 8B,
Qwen 2.5 14B, Gemma 2 9B — crossed with 3 seeds, 50 episodes × 50
steps, 95 % bootstrap CI), every defender attains skill-entropy
≥ 2.06 bits, rejects χ² phase-skill independence with p ≈ 0, and keeps
attacker belief μ_A ≥ 0.94. A naïve V1 prompt by contrast collapses to
82–100 % single-skill use, replicating the mode-collapse pathology
reported for HoneyGPT. Live Docker deployment sustains an 8 s tactical
cadence with **zero fallbacks over 224 LLM calls in a 10-minute run**
and **100 % cross-container directive delivery**. A symmetric 3 × 3
*LLM-vs-LLM* packet-level belief-tracker matrix reveals a
**same-family mirror blind spot** — diagonal cells (mean AUC = 0.74)
sustain deception 27 % longer than cross-family cells (mean
AUC = 0.58) — and a **defender-robustness inversion** in which the
smallest model (Llama 8B) exhibits the lowest cross-attacker AUC
sample standard deviation (σ = 0.015) compared with the largest
(Qwen 14B, σ = 0.281). We release
the full stack, the DeceptionScore v2 composite metric, HTUR/CPR/FSR
honey-token tracking infrastructure, Docker images, and the 9-cell
belief-trajectory dataset as an artefact. A symmetric LLM adversary
trained via fictitious play is identified as future work and already
wired as a pluggable policy stub.

---

## 1 Introduction

### 1.1 Problem Statement

Commercial and research UAS fleets expose a rich attack surface:
MAVLink control traffic (UDP 14550), onboard HTTP telemetry services
(port 80 / 8080), WebSocket control APIs modelled on agent frameworks
such as OpenClaw (port 18789), RTSP camera feeds (8554), and SSH
management consoles (22 or 2222). Documented incidents and academic
adversary models converge on a canonical five-phase campaign: (L0)
network reconnaissance, (L1) MAVLink-protocol exploitation
(HEARTBEAT spoofing, PARAM_SET abuse, unauthorised ARM / TAKEOFF),
(L2) HTTP API enumeration with credential harvest, (L3) WebSocket
CVEs and RTSP denial-of-service, and (L4) credential reuse, lateral
movement, and persistence.

**Honeydrones** — decoy UAS instances deployed to divert and profile
such adversaries — have existed since 2018 but remain technologically
conservative: today's production testbeds emit static MAVLink
replies, expose fixed port banners, and plant breadcrumb credentials
with no adaptation to the observed attacker. Against an unsophisticated
scanner this suffices; against an adversary who inspects the response
distribution or times the defender's reactions, detection happens
within the first dozen packets (Song et al., 2024).

We formulate the open research question therefore as follows:

> *Can a hierarchically-coordinated set of large language models
> produce deception that (i) maintains attacker belief ≥ 0.5 across
> multi-stage campaigns, (ii) reacts in real time to observed attacker
> phase and tool sophistication, and (iii) does so under a container-
> constrained deployment that real operators can run, with no offline
> training, using locally-served open-weight models?*

This paper answers all three sub-questions affirmatively and
quantifies the sensitivities along the way.

### 1.2 Pains and Needs

Three technical pain-points motivate the architecture we propose.

**P1 — Mode collapse under LLM-driven tactical decisions.**
Naïvely wiring an LLM into a honeypot's proactive loop produces a
collapsed policy. In our V1 prompt (a soft-preference list of phase
hints), a seed-42 run of Qwen 2.5 14 B selected the same skill
(`flight_sim`) on **100 % of 495 calls**; Llama 3.1 8 B reached
82.5 % and Gemma 2 9 B 100 % on the same number of calls. This
pathology is analogous to the *response-repetition* issue Song et al.
(2024) identify as part of the "honeypot trilemma" for LLM shell
honeypots, and is invisible to reward-based evaluation because
DeceptionEnv's heuristic reward pays roughly equally for any
non-evasive skill during the RECON-heavy early episode. Without a
principled mitigation, an LLM Tier-2 defender is *indistinguishable
from a hand-crafted Greedy baseline at the distributional level*,
nullifying the premise of using an LLM at all.

**P2 — Strategic coordination is missing from existing LLM
honeypots.** Every LLM honeypot we surveyed operates at a single
decision layer: one model decides how to reply to each packet.
Real security operations, however, couple per-host tactics to a
fleet-level policy an operator sets and revises. Existing game-
theoretic work (Horák 2019, Li 2025) models this coordination as
a POSG but implements it with scalar RL agents that cannot emit
or consume natural-language directives. A useful system needs a
channel that (i) lets an operator-facing strategic LLM shape
per-drone tactics *without* rewriting their policy at every turn,
and (ii) survives Docker-container isolation, which is the norm in
modern honeypot deployments.

**P3 — No packet-level deception-lifetime measurement.** Published
LLM-honeypot evaluations report cumulative counts ("N commands
attracted", "T seconds of engagement") or pairwise preference
scores against Cowrie. These quantities are useful but leave open
the basic question *per packet, how long does the attacker remain
deceived?* Without a trajectory, one cannot tell whether an attacker
was fooled for 10 packets and then disengaged, or fooled for 100
packets and reported back. Worse, the symmetric pairing —
**LLM defender vs. LLM attacker** — has not been measured at all in
the public literature we could locate. As LLM-driven adversaries
become tractable (see recent arXiv work on agentic cyber attackers),
the absence of such a measurement is a methodological gap that will
widen quickly.

**What the system must provide.** Addressing P1-P3 requires a design
that (a) imposes explicit phase-conditioned constraints on the LLM's
output distribution to defeat mode collapse, (b) separates fleet-level
strategic reasoning from per-drone tactical reasoning and exposes the
coupling over a robust cross-container channel, and (c) instruments
the attacker side with an independent belief estimator so the
packet-level deception lifetime can be both measured and attributed.

### 1.3 Importance

Three concrete reasons make this problem worth solving now.

**I1 — UAS threat surface is expanding, and attacker tooling is
already moving to LLMs.** Commercial UAVs now carry full Linux
companion computers; open adversary toolchains (MAVProxy,
DroneKit, Metasploit, custom ArduPilot-fuzzers) make the L0-L4
campaign accessible to intermediate attackers. In parallel,
reproducible offensive LLM agents have appeared (LLM Agent Honeypot
arXiv 2410.13919, 2024; Cloak-Honey-Trap USENIX 2025). A testbed
that only measures defender quality against *scripted* attackers
will soon be answering the wrong question.

**I2 — Deployment constraints in UAS networks rule out most
cloud-LLM solutions.** Drones and their GCS operate at the network
edge, often in contested electromagnetic environments. LLM-honeypot
infrastructure must therefore (i) run on commodity hardware, (ii)
tolerate intermittent connectivity to any remote API, and (iii) be
auditable for deployment on operator premises. This drives the
design point of **locally-served Ollama backends with
open-weight 8-14 B models** and pushes every game-theoretic and
measurement technique to operate on the data those models produce.

**I3 — Paper-grade evaluation is increasingly held to multi-metric
and multi-model standards.** Recent CCS and USENIX reviews reject
single-model, single-seed LLM evaluations as anecdotal. We therefore
commit to (i) three models spanning Meta, Alibaba, and Google-DeepMind
training regimes; (ii) 3-seed bootstrap confidence intervals;
(iii) seven game-theoretic metrics drawn from the Pawlick 2019
taxonomy; and (iv) an LLM-vs-LLM matrix whose 9 cells are reproducible
from released Docker images. The importance of *this* paper, then,
is as much methodological — demonstrating how an LLM-honeypot
system can be evaluated with the rigour that the security
community now expects — as it is systems-contribution.

### 1.4 Contributions

1. **C1 — Three-tier hierarchical LLM deception.** A Docker-deployable
   stack combining a Tier 1 GCS strategic LLM, Tier 2 per-drone
   tactical LLMs, and a Tier 3 OpenClaw-SDK lure with honey-token
   uptake tracking. A 10-minute live run exhibits 0 fallbacks over
   224 tactical LLM calls and 100 % cross-container directive
   delivery (§4).

2. **C2 — Cross-organisation LLM defender evaluation with a
   mode-collapse mitigation.** An A–E key-ed prompt with four HARD
   RULES and `repeat_penalty` raises skill entropy from 0.00-0.69 to
   2.05-2.12 bits across three models (Llama 3.1 8B, Qwen 2.5 14B,
   Gemma 2 9B) and nine seed-runs, with χ² phase-skill independence
   rejected (p = 0.0000) and Cramér's V ≥ 0.32 (Cohen large) in every
   run (§5).

3. **C3 — LLM-vs-LLM packet-level deception-lifetime evaluation.**
   A 3 × 3 attacker-belief matrix reveals (i) a **same-family mirror
   blind spot** (mean diagonal AUC 0.74 vs. off-diagonal 0.58; +27 %),
   (ii) a **defender-robustness inversion** (Llama 8 B sample
   σ = 0.015 vs. Qwen 14 B sample σ = 0.281), and (iii) a systemic
   weakness — "API token handed out too easily" — cited 51 times
   across nine cells, pointing to a concrete Tier 3 hardening target
   (§5.3).

4. **C4 — Open artefact.** Docker stack, three quantised models,
   V1/V2 prompt ablation data, DeceptionScore v2 composite metric,
   HTUR/CPR/FSR honey-token infrastructure, and the 9-cell belief
   trajectory dataset are released.

---

## 2 Related Work

### 2.1 Drone honeypots

The seminal **HoneyDrone** (Daubert et al., *NOMS*, 2018) introduced
the first UAV-specific honeypot, emulating MAVLink, Telnet, SSH, FTP,
sensor surfaces, and an internal filesystem on low-cost Raspberry Pi
hardware. Subsequent work generalised this to fleet-level deployment:
*"Optimizing Effectiveness and Defense of Drone Surveillance Missions
via Honey Drones"* (ACM TOIT, 2024) introduced mobile honey drones
with dynamically-tuned RF signal strengths, optimised via deep
reinforcement learning to counter denial-of-service adversaries.
Parallel work on cooperative UAV defence by Zhang et al.
(*"Collaborative Honeypot Defense in UAV Networks: A Learning-Based
Game Approach"*, 2023) framed honeypot selection as a Markov game
between the UAV fleet and the adversary, solved with alternating
best-response. None of these systems integrates LLM-driven
decision-making: the tactical policy is either a hand-tuned heuristic
or a scalar DRL value function. MIRAGE-UAS inherits the UAV-specific
protocol fidelity requirements (MAVLink v2 ardupilotmega dialect) but
replaces the tactical decision layer with an LLM tool-use agent
coordinated by a distinct strategic LLM.

### 2.2 LLM-powered honeypots

**HoneyGPT** (Song et al., arXiv:2406.01882, 2024) showed
that prompt-engineered GPT-3.5/4 can operate a shell honeypot that
outperforms Cowrie and real OS baselines on attack success and
temptation over three months of live Internet exposure. The system
addresses the honeypot *trilemma* — flexibility, interaction fidelity,
deception — with chain-of-thought memory pruning. **HoneyLLM**
(ICICS, 2024) ports this to a medium-interaction setup with structured
persona prompts; **LLMHoney** (arXiv:2509.01463) adds real-time
dynamic response generation; **VelLMes** and **LLMPot** extend
coverage to SCADA/ICS, POP3/SMTP, LDAP, and MySQL. The recent SoK
*"Honeypots & LLMs, More Than the Sum of Their Parts?"*
(arXiv:2510.25939, 2025) provides a canonical architecture, an
evaluation framework, and an attacker trichotomy. Crucially, every
one of these prior systems operates on *single-host* network services.
MIRAGE-UAS is, to our knowledge, the first LLM-honeypot specialised
for UAV telemetry and control-plane traffic, and the first to
instrument an attacker-side LLM belief tracker that reports
packet-level deception lifetime.

### 2.3 Hierarchical RL–LLM deception

Closest to MIRAGE-UAS in *architectural pattern* is
*"Network- and Device-Level Cyber Deception for Contested Environments
Using RL and LLMs"* (arXiv:2603.17272, 2025), which pairs a
network-level RL agent (controlling traffic redirection) with a
constrained LLM module (generating protocol-consistent responses).
The two form a closed feedback loop that improves deception
effectiveness over time. MIRAGE-UAS differs in four concrete ways:
(i) **domain specificity** — we target UAV MAVLink fleets rather than
generic network traffic, so protocol-fidelity constraints (CRC,
ardupilotmega message IDs, sysid consistency across rotation windows)
are load-bearing and dictate the tactical skill set; (ii) **three
tiers rather than two**, with a distinct Tier 1 GCS strategic agent
emitting directives over a dedicated UDP 19995 channel at a strategic
30-s cadence while Tier 2 tactical agents run at 8 s; (iii) **fleet
coordination** — a single Tier 1 commander governs three
honeydrones, and the directive schema carries soft skill biases that
propagate to the tactical LLM prompt as instruction rather than hard
override; and (iv) **cross-organisation benchmark** — we evaluate
five locally-served LLMs from distinct organisations against four
learned / game-theoretic baselines, whereas prior hierarchical work
reported on a single model family.

### 2.4 UAV / MAVLink security

MAVLink v2.0 design weaknesses — absent MAC, weak sequence-number
verification, unauthenticated PARAM_SET, default-broadcast message
patterns — are documented in recent top-tier security-venue work.
**Veksler, Akkaya and Uluagac** (*ACM AsiaCCS*, 2024) demonstrate
high-throughput *covert channels* that exfiltrate drone data through
MAVLink's default-broadcast messages and redundant field layouts,
even when an active warden inspects the ground-control-station link.
**Schiller et al.** (*NDSS*, 2023) reverse-engineer DJI's proprietary
DroneID protocol with commercial off-the-shelf SDR hardware and
disclose sixteen firmware-level CVEs across two DJI platforms,
undermining the widely-held assumption that DroneID position data are
encrypted. The CVE surface motivating our attacker simulator's L0-L4
campaign derives from this literature (MAVLink heartbeat spoofing,
mission-item injection, parameter-set abuse, log-transfer abuse for
exfiltration, default-broadcast covert leakage). MIRAGE-UAS
complements authentication-layer defences by adding a deception
layer that operates orthogonally — an attacker who bypasses MAC
checks, decodes vendor telemetry, or rides covert channels still
interacts with a honeydrone whose responses are shaped by a
hierarchical LLM policy rather than a static script.

### 2.5 Game-theoretic foundations of cyber deception

Defensive deception has been studied as an asymmetric-information
game for over a decade. **Pawlick, Colbert and Zhu** (*ACM Computing
Surveys*, 2019) consolidate this line of work into a six-species
taxonomy — *perturbation, moving target defense, obfuscation, mixing,
honey-x,* and *attacker engagement* — and survey the Stackelberg,
Nash and signalling-game formulations that solve each species. Our
three-tier architecture sits at the honey-x / attacker-engagement
boundary.

**POSG honeypot placement.** **Horák et al.**
(*Computers & Security*, 2019) formulate dynamic-lateral-movement
honeypot allocation as a Partially Observable Stochastic Game (POSG)
in which the defender has full state visibility and the attacker
observes only local network responses. MIRAGE-UAS inherits this POSG
view: state includes per-drone phase, attacker-tool level, dwell
time, and honey-token issuance; the defender observes all of it,
while the attacker sees only response-quality proxies derived from
MAVLink / HTTP / WebSocket replies.

**MTD + honeynet integration.** **Li et al.** (*Information Sciences*,
2025) introduce **GH-MTD**, a framework combining traffic detection,
a game-theoretic action selector, MTD mutation, and honeynet probes;
they report a 5.5 × diversion improvement over conventional honeypots
and a 3.4 × capture rate. Our system covers the analogous blocks —
detection via `EngagementTracker`, game-theoretic action via
Signaling-EQ / DQN / LLM, MTD via sysid-and-port rotation, and the
honeynet via Tier 3 OpenClaw-SDK emulation — and extends the
prescription by using LLM tactical policies in place of hand-crafted
MTD mutation rules and by adding a Tier 1 strategic layer.

**Signalling games and QRE.** Our Signaling-EQ policy implements the
Quantal Response Equilibrium of **McKelvey & Palfrey** (1995) as a
logit-response approximation of a cheap-talk sender–receiver game
(**Crawford & Sobel**, 1982). Prior applied work in cyber-deception
QRE includes **Pawlick & Zhu** (*CDC*, 2021), **Khouzani et al.**
(2019), and the classical honeypot foundation of
**Carroll & Grosu** (2011).

**Adaptive timing via Stackelberg RL.** **Carnevale et al.**
(arXiv:2505.21244, 2025) pair Stackelberg commitment with deep RL
(AASGRL) to decide *when* a deception should be triggered. Our
proactive-loop cadence (8 s tactical, 30 s strategic) plays this role
in a fixed scheduling regime; an adaptive-timing extension is future
work. **Prakash & Wellman** (*GameSec*, 2015) show the
empirical-game-theoretic-analysis (EGTA) methodology of solving MTD
games from empirical simulations — closely matching our own approach
of composing a game from empirically-learned policies.

**Hypergame view of the perception gap.** **Gutierrez et al.** (2022)
apply hypergame theory to honeypot deception, modelling the
*perception gap* between attacker and defender: the two players are
playing different games because they believe different things. We
adopt this lens for the **hypergame stability** metric (§5), the
fraction of engagement time during which the attacker's perceived
game (thinking they are attacking a real UAS) diverges from the actual
game (they are in a honeydrone).

**LLM-agent games.** A 2024 arXiv line of work (arXiv:2507.10621 and
adjacent) formalises LLM agents as game players, opening the door to
defender-LLM-vs-attacker-LLM experiments. Our pluggable
`AttackerPolicy` abstraction instantiates this framing — the scripted
L0-L4 adversary can be replaced with an LLM-driven one to run the
same fictitious-play protocol we already use for DQN, closing the
symmetric evaluation loop that MIRAGE-UAS's belief-tracker matrix
(§5.3) begins.

### 2.6 Agent frameworks and OpenClaw

OpenClaw (openclaw.im) is an open-source TypeScript AI-agent gateway
originally designed for personal messaging-channel orchestration. We
reuse OpenClaw's *gateway–tool* abstraction pattern at Tier 1 — a
single process mediates operator requests, tool invocation, and
per-session LLM context — but implement the gateway in Python against
locally-served Ollama backends. The choice retains the architectural
affordances of OpenClaw (session-per-peer tool sandboxing, directive
routing) while avoiding the Node.js runtime and OAuth dependencies
unnecessary for a research testbed. Tier 3 separately emulates the
OpenClaw SDK wire format purely as a lure, exploiting the framework's
public visibility to attract sophisticated attackers who recognise
the interface.

Tier 1 (`mirage-gcs:latest`) and Tier 2 / Tier 3
(`mirage-honeydrone:latest`) are deployed as separate Docker
containers on a shared `honey_net` + `internal` network pair. LLM
inference is always delegated to a host-side Ollama instance (Windows
host in our WSL 2 setup, reachable at 172.23.240.1:11434); no
language model ever runs inside a container. Directive delivery
between tiers uses UDP 19995 across Docker service-DNS
(`cc_honey_0N`). A 10-minute live run of the full stack sustained 75
LLM decisions per drone, zero fallbacks across 224 calls, and 100 %
cross-container directive delivery.

### 2.7 Positioning summary

MIRAGE-UAS sits at the intersection of four prior lines of work:
UAV-specific honeydrone testbeds (§2.1), LLM-driven single-host
honeypots (§2.2), hierarchical RL-LLM deception for generic networks
(§2.3), and game-theoretic deception foundations (§2.5). None of
these lines previously combined *fleet-level* strategic LLM control
with *per-drone* tactical LLM deception, released a Docker-ready
testbed, or measured packet-level deception lifetime under
symmetric LLM-vs-LLM evaluation. The rest of this paper presents the
system (§3), the prompt-engineering mitigation that makes LLM
tactical deception non-trivial (§4), and a five-metric plus
seven-game-theoretic-metric evaluation supplemented by a 3 × 3
cross-model belief-trajectory matrix (§5).

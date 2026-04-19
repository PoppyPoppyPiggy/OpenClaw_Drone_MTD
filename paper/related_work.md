# MIRAGE-UAS — Related Work (§2 draft)

## §2.1 Drone Honeypots

The seminal **HoneyDrone** by Daubert et al. (NOMS 2018) introduced the
first UAV-specific honeypot, emulating MAVLink, Telnet, SSH, FTP, sensor
surfaces, and an internal filesystem on low-cost Raspberry Pi hardware.
Subsequent work generalised this to fleet-level deployment:
*"Optimizing Effectiveness and Defense of Drone Surveillance Missions via
Honey Drones"* (ACM TOIT 2024) introduced mobile honey drones with
dynamically-tuned RF signal strengths, optimised via deep reinforcement
learning to counter Denial-of-Service adversaries. Parallel work on
cooperative UAV defence by Zhang et al. (*"Collaborative Honeypot Defense
in UAV Networks: A Learning-Based Game Approach"*, 2023) framed honeypot
selection as a Markov game between UAV fleet and adversary, solved with
alternating best-response. None of these systems integrate LLM-driven
decision-making: the policy is either a hand-tuned heuristic or a scalar
DRL value function. MIRAGE-UAS inherits the UAV-specific protocol fidelity
requirements (MAVLink v2 ardupilotmega dialect) but replaces the tactical
decision layer with an LLM tool-use agent.

## §2.2 LLM-Powered Honeypots

**HoneyGPT** (Song et al., arXiv:2406.01882, USENIX track) showed that
prompt-engineered GPT-3.5/4 can operate a shell honeypot that outperforms
Cowrie and real OS baselines on attack-success and temptation over
three months of live Internet exposure. The system addresses the
honeypot *trilemma* — flexibility, interaction fidelity, deception —
with chain-of-thought memory pruning. **HoneyLLM** (ICICS 2024) ports
this to a medium-interaction setup with structured persona prompts;
**LLMHoney** (arXiv:2509.01463) adds real-time dynamic response
generation; **VelLMes** and **LLMPot** extend coverage to SCADA/ICS,
POP3/SMTP, LDAP, and MySQL. The recent SoK *"Honeypots & LLMs, More Than
the Sum of Their Parts?"* (arXiv:2510.25939) provides a canonical
architecture, evaluation framework, and attacker trichotomy. All of this
prior work targets **single-host network services**. MIRAGE-UAS is, to
our knowledge, the first LLM-honeypot specialised for UAV telemetry and
control plane traffic.

## §2.3 Hierarchical RL-LLM Deception

Closest to MIRAGE-UAS in *architectural pattern* is
*"Network- and Device-Level Cyber Deception for Contested Environments
Using RL and LLMs"* (arXiv:2603.17272), which pairs a network-level RL
agent (controlling traffic redirection) with a constrained LLM module
(generating protocol-consistent responses). The two form a closed
feedback loop that improves deception effectiveness over time. MIRAGE-UAS
differs in four concrete ways:

1. **Domain specificity.** We target UAV MAVLink fleets rather than
   generic network traffic. Protocol fidelity constraints (CRC,
   ardupilotmega message IDs, sysid consistency across rotation
   windows) are load-bearing and dictate the tactical skill set.
2. **Three tiers instead of two.** A distinct Tier 1 *GCS strategic
   agent* issues directives over a dedicated UDP control channel
   (§4.3). The tier boundary is not merely conceptual — Tier 1 runs a
   larger 14B-class model (Qwen 2.5) at 30 s cadence, while Tier 2
   honeydrone agents run smaller 8–14B models at 8 s cadence. Tier 3
   is an attacker-facing LLM-SDK emulation that captures L3–L4
   interactions.
3. **Fleet coordination.** A single Tier 1 commander coordinates three
   heterogeneously-tasked honeydrones; the directive schema carries
   soft skill biases that propagate to the tactical LLM prompt as
   instruction rather than hard override.
4. **Cross-organisation benchmark.** We evaluate five locally-served
   LLMs from distinct organisations (Llama 3.1 — Meta; Qwen 2.5 —
   Alibaba; Phi-4 — Microsoft; Mistral-Nemo — Mistral AI; Gemma 2 —
   Google) against four learned/game-theoretic baselines. Prior
   hierarchical work evaluated on a single model family.

## §2.4 UAV / MAVLink Security

MAVLink v2.0 design vulnerabilities — absent MAC, weak sequence-number
verification, unauthenticated PARAM_SET — are documented in recent IEEE
work (Li et al., 2024). The CVE surface motivating our attacker
simulator's L0-L4 campaign derives from this literature (MAVLink
heartbeat spoofing, mission-item injection, parameter-set abuse,
log-transfer abuse for exfiltration). MIRAGE-UAS complements
authentication-layer defences by adding a deception layer that operates
orthogonally — an attacker who bypasses MAC checks still interacts with
a honeydrone.

## §2.5 Game-Theoretic Foundations of Cyber Deception

Defensive deception has been studied as an asymmetric-information
game for over a decade. **Pawlick, Colbert and Zhu** (*ACM
Computing Surveys*, 2019) consolidate this line of work into a
six-species taxonomy — *perturbation, moving target defense,
obfuscation, mixing, honey-x, and attacker engagement* — and survey
the Stackelberg, Nash and signalling-game formulations that solve
each species. Our three-tier architecture sits at the
honey-x / attacker-engagement boundary: Tier 3 plants UUID-tagged
honey-tokens (honey-x), while Tier 1 / Tier 2 direct live attacker
engagement through strategic-directive and tactical-skill channels.
Two strands are especially load-bearing for our design.

### §2.5.1 Signalling games, QRE, and Signaling-EQ

Our Signaling-EQ policy implements the Quantal Response Equilibrium
of **McKelvey & Palfrey** (1995) as a logit-response approximation
of a cheap-talk sender–receiver game (**Crawford & Sobel**, 1982).
Prior applied work in cyber-deception QRE includes **Pawlick & Zhu**
(*CDC*, 2021) and **Khouzani et al.** (2019). MIRAGE-UAS contributes
an online EMA-corrected version that updates belief-shift estimates
from actual engagement outcomes within a session. **Carroll & Grosu**
(2011) give the classical game-theoretic foundation for strategic
honeypot deployment that our signalling engine builds on.

### §2.5.2 POSG honeypot placement

**Horák et al.** (*Computers & Security*, 2019) formulate
dynamic-lateral-movement honeypot allocation as a Partially
Observable Stochastic Game (POSG) in which the defender has full
state visibility and the attacker observes only local network
responses. The resulting defender policy reallocates honeypots as
the attacker traverses the attack graph. MIRAGE-UAS inherits this
POSG view: state includes per-drone phase, attacker-tool level,
dwell time, and honey-token issuance; the defender observes all of
it, while the attacker sees only response-quality proxies derived
from MAVLink / HTTP / WebSocket replies.

### §2.5.3 MTD + honeynet integration

**Li et al.** (*Information Sciences*, 2025) introduce **GH-MTD**, a
framework combining traffic detection, a game-theoretic action
selector, MTD mutation, and honeynet probes; they report a 5.5×
diversion improvement over conventional honeypots and a 3.4×
capture rate. Our system covers the analogous blocks — detection
via EngagementTracker, game-theoretic action via
Signaling-EQ / DQN / LLM, MTD via sysid-and-port rotation, and the
honeynet via Tier 3 OpenClaw-SDK emulation — and extends the
prescription by (i) using LLM tactical policies in the place of
hand-crafted MTD mutation rules, and (ii) adding a Tier 1 strategic
layer.

### §2.5.4 Adaptive timing via Stackelberg RL

**Carnevale et al.** (*arXiv:2505.21244*, 2025) — "When to Deceive:
A Cross-Layer Stackelberg Game Framework for Strategic Timing of
Cyber Deception" — pair Stackelberg commitment with deep RL
(AASGRL) to decide *when* a deception should be triggered. Our
proactive loop cadence (8 s tactical, 30 s strategic) plays the
same role in a fixed scheduling regime; an adaptive-timing
extension is future work. **Prakash & Wellman** (*GameSec*, 2015)
show the empirical-game-theoretic-analysis (EGTA) methodology of
solving MTD games from empirical simulations — closely matching our
own approach of composing a game from empirically-learned policies.

### §2.5.5 UAV-specific honeypot games

**Zhang et al.** (2023) — "Collaborative Honeypot Defense in UAV
Networks: A Learning-Based Game Approach" — models fleet-level
honeypot coordination as a Markov game and solves it via
alternating best-response. MIRAGE-UAS differs by (i) using
MAVLink-fidelity telemetry and a realistic attacker-tool hierarchy,
(ii) coordinating through an LLM-mediated strategic layer, and
(iii) evaluating against a cross-organisation suite of LLMs.

### §2.5.6 Hypergame view of the perception gap

**Gutierrez et al.** (2022) apply hypergame theory to honeypot
deception, modelling the *perception gap* between attacker and
defender: the two players are playing different games because they
believe different things. We adopt this lens for the **hypergame
stability** metric (Table VIII), i.e., the fraction of engagement
time during which the attacker's perceived game (thinking they are
attacking a real UAS) diverges from the actual game (they are in a
honeydrone).

### §2.5.7 LLM-agent games

A 2024 arXiv line of work (*arXiv:2507.10621* and adjacent)
formalises LLM agents as game players, opening the door to
defender-LLM-vs-attacker-LLM experiments. Our pluggable
`AttackerPolicy` abstraction instantiates this framing — the
scripted L0–L4 adversary used in this paper can be replaced with
an LLM-driven one to run the same fictitious-play protocol we
already use for DQN, closing the symmetric-evaluation loop in
future work. We report Table VIII (§5) with four game-theoretic
metrics grounded in these foundations: belief manipulation
(Pawlick), information leakage / skill entropy (Horák), deception
ratio (policy-distribution similarity across phases), and hypergame
stability (Gutierrez).

## §2.6 Agent Frameworks and OpenClaw

OpenClaw (openclaw.im) is an open-source TypeScript AI-agent gateway
originally designed for personal messaging-channel orchestration. We
reuse OpenClaw's *gateway-tool* abstraction pattern at Tier 1 — a
single process mediates operator requests, tool invocation, and
per-session LLM context — but implement the gateway in Python against
locally-served Ollama backends. The choice retains the architectural
affordances of OpenClaw (session-per-peer tool sandboxing, directive
routing) while avoiding runtime and OAuth dependencies unnecessary for
a research testbed. Tier 3 separately emulates the OpenClaw SDK wire
format purely as a lure, exploiting the framework's public visibility
to attract sophisticated attackers who recognise the interface.

### Deployment (2026-04-19 update)

Tier 1 (`mirage-gcs:latest`) and Tier 2 / Tier 3
(`mirage-honeydrone:latest`) are deployed as separate Docker containers
on a shared `honey_net` + `internal` network pair. LLM inference is
always delegated to a host-side Ollama instance (Windows host in our
WSL 2 setup, reachable at `172.23.240.1:11434`); no language model ever
runs inside a container. Directive delivery between tiers uses UDP 19995
across Docker service-DNS (`cc_honey_0N`). A 10-minute live run of the
full stack sustained 75 LLM decisions per drone, 0 fallbacks across
224 calls, and 100 % cross-container directive delivery. See
`DOCKER_INTEGRATION_REPORT.md §3-4` for detailed measurements.

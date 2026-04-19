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

## §2.5 Deception Game Theory and QRE

Our Signaling-EQ policy implements the Quantal Response Equilibrium of
McKelvey & Palfrey (1995) as a logit-response approximation of a
cheap-talk sender-receiver game (Crawford & Sobel 1982). Prior applied
work in cyber-deception QRE includes Pawlick & Zhu (2021, CDC) and the
signalling-game application of Khouzani et al. (2019). MIRAGE-UAS
contributes an online EMA-corrected version that updates belief-shift
estimates from actual engagement outcomes within a session.

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

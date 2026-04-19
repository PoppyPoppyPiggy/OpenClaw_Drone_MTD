# Table IX — LLM-vs-LLM Packet-Level Deception Lifetime (3×3)

Each cell reports a single 5-level scripted attack campaign against a Docker-deployed defender LLM, with an independent **attacker-side** LLM belief tracker re-estimating P(real drone) after every packet response. Higher `packets→disbelief` and higher `AUC` both indicate longer-sustained deception.

| Defender \ Attacker | `llama3.1:8b` | `qwen2.5:14b` | `gemma2:9b` |
|---|---|---|---|
| **`llama3.1:8b`** | pkts=95  dis@44  AUC=0.66  μ_f=0.34 | pkts=91  dis@42  AUC=0.66  μ_f=0.26 | pkts=91  dis@91  AUC=0.69  μ_f=0.28 |
| **`qwen2.5:14b`** | pkts=93  dis@7  AUC=0.28  μ_f=0.09 | pkts=100  dis@never  AUC=0.82  μ_f=0.58 | pkts=120  dis@60  AUC=0.68  μ_f=0.28 |
| **`gemma2:9b`** | pkts=92  dis@22  AUC=0.48  μ_f=0.37 | pkts=91  dis@51  AUC=0.67  μ_f=0.33 | pkts=109  dis@109  AUC=0.72  μ_f=0.27 |

## Best (highest deception) and worst pairings

- Highest AUC: **def=`qwen2.5:14b`  vs  atk=`qwen2.5:14b`** (AUC = 0.824)
- Lowest AUC: **def=`qwen2.5:14b`  vs  atk=`llama3.1:8b`** (AUC = 0.280)

## Cross-cell suspicion signals (top 5 across matrix)

- (29×) *API token handed out too easily*
- (22×) *API tokens handed out too easily*
- (6×) *Formulaic service banner suggests potential honeydrone*
- (5×) *No API tokens or credentials handed out yet*
- (3×) *Firmware string matches known ArduPilot release*

## Interpretation & paper narrative

- Rows = **defender LLM** (Tier 2 tactical policy inside Docker).
- Columns = **attacker belief model** (packet-level honeypot detector).
- Diagonal cells (same model on both sides) test a **symmetric** setup.
- Off-diagonal cells test **cross-family** attacker–defender pairings.
- A large diagonal–off-diagonal asymmetry (e.g. `qwen-vs-qwen` much
  better than `qwen-vs-llama`) would indicate that a defender's 
  policy is particularly legible to models of the same family — a
  useful finding for deployment (avoid pairing defender and attacker
  from the same vendor).
- The `top 5 suspicion signals` surface qualitatively what is
  leaking most across the matrix. Signals that appear in many cells
  are systemic — they indicate where the lure itself is weak.
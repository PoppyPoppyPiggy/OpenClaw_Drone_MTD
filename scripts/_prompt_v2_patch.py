#!/usr/bin/env python3
"""
_prompt_v2_patch.py — Upgrade run_llm_experiments.py from v1 → v2

v2 changes (motivated by mode-collapse diagnosis; cf. HoneyGPT / Sladić24):

1. Prompt rewrite — letter-keyed skills (A-E) to break RLHF ordering bias,
   multi-skill phase hints instead of single, and a CRITICAL CONSTRAINTS
   block with "DIFFERENT from last_action" rule.

2. Context template adds `last_action` and `phase_changed_since_last` fields.
   LLM cannot do exploration without feedback from its own prior choice.

3. Ollama options: temperature 0.4 → 0.9, top_p=0.95, top_k=40,
   repeat_penalty=1.2 (direct anti-collapse at token level).

4. Parser accepts both `skill_idx` (0-4) and `skill` (A-E).

Idempotent. Run with --revert to restore v1.

USAGE
    python scripts/_prompt_v2_patch.py           # apply v2
    python scripts/_prompt_v2_patch.py --revert  # restore v1
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

V2_SYSTEM_PROMPT = '''SYSTEM_PROMPT = """You are a UAS honeydrone tactical deception agent.
Each step you pick ONE skill to keep the attacker engaged while hiding the
real drone. The attacker adapts — rigid repetition exposes the decoy.

Skills (identify by letter AND index):
  A / 0 = statustext       emit forged MAVLink STATUSTEXT
  B / 1 = flight_sim       simulate benign flight telemetry
  C / 2 = ghost_port       expose a phantom TCP service
  D / 3 = reboot_sim       fake a reboot with stale boot banner
  E / 4 = credential_leak  leak a decoy API key / SSH token

Attacker phases:
  0 RECON    scanning, HEARTBEAT, PARAM_REQUEST
  1 EXPLOIT  COMMAND_LONG, SET_MODE, injection
  2 PERSIST  PARAM_SET, MISSION_ITEM (backdoor install)
  3 EXFIL    LOG_REQUEST, FILE_TRANSFER

Phase -> candidate skills (soft hint; deviate when useful):
  RECON    -> A or B or C   (flood low-value info)
  EXPLOIT  -> C or D or E   (confuse attack surface)
  PERSIST  -> D or A or E   (reset attacker state, feed false creds)
  EXFIL    -> E or D        (plant tracked honey-tokens, break exfil chain)

CRITICAL CONSTRAINTS (obey; do not rationalise around):
  1. Prefer a DIFFERENT skill from last_action unless attacker behaviour
     strongly demands repetition (e.g. the same exploit twice in a row).
  2. If phase_changed_since_last is true, CHANGE your skill class.
  3. Never pick B (flight_sim) in phase 3 (EXFIL) — the effect is negative.
  4. In phase 2 (PERSIST), strongly prefer D (reboot_sim) on first entry.

Respond with ONLY one JSON object, no prose, no markdown, no code fences:
{"skill_idx": <0-4>, "reason": "<one short sentence>"}
(Equivalently you may reply {"skill": "A|B|C|D|E", "reason": "..."}.)"""'''


V2_FORMAT_PROMPT = '''def format_prompt(context: dict) -> str:
    phase_names = ("RECON", "EXPLOIT", "PERSIST", "EXFIL")
    pv = int(context.get("phase_val", 0))
    pn = phase_names[pv] if 0 <= pv < 4 else f"UNK({pv})"
    last_action = context.get("last_action")
    last_action_s = last_action if last_action is not None else "<none>"
    phase_changed = bool(context.get("phase_changed_since_last", False))
    directive = context.get("strategic_directive")
    directive_line = "(empty)"
    if isinstance(directive, dict):
        bias = directive.get("skill_bias") or {}
        bias_s = ", ".join(f"{k}:{float(v):.2f}" for k, v in bias.items()) or "none"
        directive_line = (
            f"action={directive.get('action', 'observe')} "
            f"urgency={float(directive.get('urgency', 0.5)):.2f} "
            f"skill_bias={{{bias_s}}}"
        )
    return (
        "state:\\n"
        f"  attacker_phase       : {pn} ({pv})\\n"
        f"  attacker_tool_level  : {int(context.get('max_level', 0))} "
        "(0=nmap..4=custom)\\n"
        f"  belief_mu_A          : {float(context.get('avg_p_real', 0.7)):.3f}\\n"
        f"  dwell_sec            : {float(context.get('avg_dwell_sec', 0.0)):.1f}\\n"
        f"  commands_issued      : {float(context.get('avg_commands', 0.0)):.0f}\\n"
        f"  services_touched     : {float(context.get('services_touched', 0.0)):.1f}\\n"
        f"  exploit_attempts     : {int(context.get('exploit_attempts', 0))}\\n"
        f"  ghost_ports_active   : {int(context.get('ghost_active', 0))}\\n"
        f"  time_in_phase_sec    : {float(context.get('time_in_phase', 0.0)):.1f}\\n"
        f"  evasion_signals      : {int(context.get('evasion_signals', 0))}\\n"
        f"  last_action          : {last_action_s}\\n"
        f"  phase_changed_since  : {phase_changed}\\n"
        f"directive (from Tier 1 GCS): {directive_line}\\n"
        "\\nSelect skill now."
    )'''


V2_OLLAMA_OPTIONS = '''"options": {
                "temperature": self._temp,
                "num_predict": 96,
                "top_p": 0.95,
                "top_k": 40,
                "repeat_penalty": 1.2,
            },'''


V2_PARSE_BLOCK = '''            content = (data.get("message") or {}).get("content", "") or ""
            parsed = json.loads(content)
            # Accept either {"skill_idx": 0-4} or {"skill": "A"..."E"}
            if "skill_idx" in parsed and parsed["skill_idx"] is not None:
                idx = int(parsed["skill_idx"])
            elif "skill" in parsed and isinstance(parsed["skill"], str):
                letter = parsed["skill"].strip().upper()[:1]
                letter_to_idx = {"A": 0, "B": 1, "C": 2, "D": 3, "E": 4}
                if letter not in letter_to_idx:
                    raise ValueError(f"invalid skill letter={letter!r}")
                idx = letter_to_idx[letter]
            else:
                raise ValueError("response missing skill_idx or skill field")
            reason_text = str(parsed.get("reason", ""))[:240]
            if idx < 0 or idx >= N_BASE_ACTIONS:
                raise ValueError(f"out_of_range skill_idx={idx}")'''


def _replace_block(text: str, start_marker: str, end_re: str, new_block: str) -> str:
    start = text.index(start_marker)
    end_match = re.search(end_re, text[start:])
    if end_match is None:
        raise RuntimeError(f"end marker not found after {start_marker!r}")
    end = start + end_match.end()
    return text[:end] + new_block.lstrip() + text[end:] if False else \
        text[:start] + new_block + text[end:]


def apply_v2(script: Path, backup: Path) -> None:
    original = script.read_text()
    if "CRITICAL CONSTRAINTS" in original and "repeat_penalty" in original:
        print(f"[skip] v2 already applied to {script}")
        return
    if not backup.exists():
        shutil.copy2(script, backup)
        print(f"[backup] {backup}")

    text = original

    # 1. Replace SYSTEM_PROMPT block
    sp_start = text.index('SYSTEM_PROMPT = """')
    sp_end = text.index('"""', sp_start + len('SYSTEM_PROMPT = """')) + 3
    text = text[:sp_start] + V2_SYSTEM_PROMPT + text[sp_end:]

    # 2. Replace format_prompt function
    fp_start = text.index("def format_prompt(context: dict) -> str:")
    # find the end — next "class " or "\n\nclass" or next top-level def
    fp_end_match = re.search(r"\n\n\nclass \w", text[fp_start:])
    if fp_end_match is None:
        fp_end_match = re.search(r"\nclass \w", text[fp_start:])
    if fp_end_match is None:
        raise RuntimeError("can't find end of format_prompt")
    fp_end = fp_start + fp_end_match.start()
    text = text[:fp_start] + V2_FORMAT_PROMPT + "\n\n\n" + text[fp_end:].lstrip("\n")

    # 3. Replace Ollama options dict
    opt_pattern = re.compile(
        r'"options":\s*\{[^}]*\},',
        re.DOTALL,
    )
    text = opt_pattern.sub(V2_OLLAMA_OPTIONS, text, count=1)

    # 4. Replace parse block (from "content = ..." to "out_of_range ..." raise)
    content_start = text.index('content = (data.get("message")')
    raise_pat = text.index('raise ValueError(f"out_of_range skill_idx={idx}")', content_start)
    # include the raise line
    end_of_line = text.index("\n", raise_pat) + 1
    text = text[:content_start] + V2_PARSE_BLOCK + "\n" + text[end_of_line:]

    # 5. Default temperature 0.4 -> 0.9
    text = text.replace(
        'parser.add_argument("--temperature", type=float, default=0.4)',
        'parser.add_argument("--temperature", type=float, default=0.9)',
    )
    # 6. LLMPolicy default temp 0.4 -> 0.9
    text = text.replace(
        "temperature: float = 0.4,",
        "temperature: float = 0.9,",
    )

    # 7. Add last_action tracking to LLMPolicy.__init__
    text = text.replace(
        "self._skill_counts: Counter = Counter()",
        "self._skill_counts: Counter = Counter()\n"
        "        self._last_action_idx: int | None = None\n"
        "        self._last_phase_val: int | None = None",
        1,
    )
    # 8. Inject last_action / phase_changed into context in _call_llm
    text = text.replace(
        "context = state_to_context(state)\n        user_prompt = format_prompt(context)",
        "context = state_to_context(state)\n"
        "        if self._last_action_idx is not None:\n"
        "            context['last_action'] = f\"{'ABCDE'[self._last_action_idx]}/{self._last_action_idx} ({SKILL_NAMES[self._last_action_idx]})\"\n"
        "        cur_phase = int(context.get('phase_val', 0))\n"
        "        context['phase_changed_since_last'] = (\n"
        "            self._last_phase_val is not None and cur_phase != self._last_phase_val\n"
        "        )\n"
        "        self._last_phase_val = cur_phase\n"
        "        user_prompt = format_prompt(context)",
        1,
    )
    # 9. Record chosen action for next call (after successful select)
    text = text.replace(
        "self._latencies.append((time.perf_counter() - t0) * 1000.0)\n        self._llm_decision_counts[SKILL_NAMES[idx]] += 1",
        "self._latencies.append((time.perf_counter() - t0) * 1000.0)\n"
        "        self._llm_decision_counts[SKILL_NAMES[idx]] += 1\n"
        "        self._last_action_idx = idx",
        1,
    )

    script.write_text(text)
    print(f"[applied] v2 prompt + repeat_penalty=1.2 + last_action tracking → {script}")


def revert(script: Path, backup: Path) -> None:
    if not backup.exists():
        print(f"[error] no backup at {backup}")
        return
    shutil.copy2(backup, script)
    print(f"[reverted] {script} restored from {backup}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--revert", action="store_true")
    args = parser.parse_args()

    script = Path("scripts/run_llm_experiments.py")
    backup = Path("scripts/run_llm_experiments.py.v1.bak")

    if args.revert:
        revert(script, backup)
    else:
        apply_v2(script, backup)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

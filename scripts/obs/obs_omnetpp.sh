#!/usr/bin/env bash
# OMNeT++ Terminal — watch trace outputs and file generation
cd "$(dirname "$0")/../.."

echo -e "\033[45;97m  OMNET++ TRACES  \033[0m"
echo "Watching: omnetpp_trace/ and results/omnetpp/"
echo "────────────────────────────────────────────────────────────"

TRACE_DIR="omnetpp_trace"
RESULTS_OMNET="results/omnetpp"

while true; do
    echo -e "\033[90m$(date '+%H:%M:%S')\033[0m"

    # Check trace directory
    if [ -d "$TRACE_DIR" ]; then
        for f in "$TRACE_DIR"/*; do
            [ -f "$f" ] || continue
            name=$(basename "$f")
            size=$(stat -c %s "$f" 2>/dev/null || echo 0)
            lines=""
            if [[ "$name" == *.csv ]]; then
                lines=" ($(wc -l < "$f") lines)"
            elif [[ "$name" == *.xml ]]; then
                lines=" ($(grep -c '<' "$f" 2>/dev/null) tags)"
            elif [[ "$name" == *.ini ]]; then
                lines=" ($(wc -l < "$f") lines)"
            fi
            # Color by recency
            age=$(( $(date +%s) - $(stat -c %Y "$f" 2>/dev/null || echo 0) ))
            if [ "$age" -lt 60 ]; then
                color="\033[92m"  # green = fresh
                status="FRESH"
            elif [ "$age" -lt 300 ]; then
                color="\033[93m"  # yellow = recent
                status="${age}s ago"
            else
                color="\033[90m"  # gray = stale
                status="${age}s ago"
            fi
            printf "  ${color}%-30s %8s bytes %s %s\033[0m\n" "$name" "$size" "$lines" "$status"
        done
    else
        echo -e "  \033[90momnetpp_trace/ not found — run: python3 -m src.omnetpp.trace_exporter\033[0m"
    fi

    # Check for .sca/.vec files (OMNeT++ simulation output)
    if [ -d "$RESULTS_OMNET" ]; then
        sca_count=$(find "$RESULTS_OMNET" -name "*.sca" 2>/dev/null | wc -l)
        vec_count=$(find "$RESULTS_OMNET" -name "*.vec" 2>/dev/null | wc -l)
        if [ "$sca_count" -gt 0 ] || [ "$vec_count" -gt 0 ]; then
            echo -e "  \033[92mSimulation results: ${sca_count} .sca, ${vec_count} .vec\033[0m"
        fi
    fi

    # Check replay.ini
    if [ -f "$TRACE_DIR/replay.ini" ]; then
        sim_time=$(grep "sim-time-limit" "$TRACE_DIR/replay.ini" 2>/dev/null | head -1)
        network=$(grep "^network" "$TRACE_DIR/replay.ini" 2>/dev/null | head -1)
        echo -e "  \033[96mConfig: $network | $sim_time\033[0m"
    fi

    echo ""
    sleep 5
done

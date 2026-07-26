#!/bin/bash
#
# Scansnap PDF Processor - launchd WatchPaths version
# Triggered by launchd when ~/Scansnap changes. Processes all pending PDFs and exits.
#

MONITOR_DIR="$HOME/Scansnap"
MONITOR_DIR_OCRED="$HOME/Scansnap-OCRed"
SCRIPT_DIR="/Users/hirom/Projects/OCR"
PYTHON="$SCRIPT_DIR/.venv/bin/python"
PROCESSOR="$SCRIPT_DIR/process_pdf.py"
LOG_FILE="$HOME/Library/Logs/scansnap_watcher.log"
LOCK_FILE="/tmp/scansnap_processor.lock"

mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$MONITOR_DIR"
mkdir -p "$MONITOR_DIR_OCRED"

log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

# Prevent concurrent runs (launchd may re-trigger while processing)
if ! mkdir "$LOCK_FILE" 2>/dev/null; then
    log "Already running, skipping."
    exit 0
fi
trap 'rm -rf "$LOCK_FILE"' EXIT

if [ ! -f "$PYTHON" ]; then
    log "ERROR: Python not found at $PYTHON"
    exit 1
fi

# Wait for file to stabilize (size stops changing)
wait_for_stable() {
    local file="$1"
    local prev_size=-1
    local curr_size

    sleep 2  # Initial wait for ScanSnap to finish writing

    while true; do
        if [ ! -f "$file" ]; then
            return 1
        fi

        curr_size=$(stat -f%z "$file" 2>/dev/null)
        if [ -z "$curr_size" ]; then
            return 1
        fi

        if [ "$curr_size" -eq "$prev_size" ]; then
            return 0
        fi

        prev_size=$curr_size
        sleep 1
    done
}

# Process a single PDF file
# $1: path, $2: "ocr" to run OCR, "skip-ocr" to bypass OCR
process_file() {
    local file="$1"
    local mode="$2"
    local filename
    filename=$(basename "$file")

    # Ignore hidden files and temp files
    if [[ "$filename" == .* ]] || [[ "$filename" == ~* ]]; then
        return
    fi

    if [[ ! "$filename" =~ \.[pP][dD][fF]$ ]]; then
        return
    fi

    log "Detected [$mode]: $filename"

    if ! wait_for_stable "$file"; then
        log "  File disappeared: $filename"
        return
    fi

    log "  Processing..."
    local extra_args=()
    if [ "$mode" = "skip-ocr" ]; then
        extra_args=(--skip-ocr)
    fi

    if "$PYTHON" "$PROCESSOR" "$file" "${extra_args[@]}" >> "$LOG_FILE" 2>&1; then
        log "  Completed: $filename"
    else
        log "  ERROR: Failed to process $filename"
    fi
}

# Process all pending PDFs
found=0

# Scansnap: needs OCR
for pdf in "$MONITOR_DIR"/*.pdf "$MONITOR_DIR"/*.PDF; do
    if [ -f "$pdf" ]; then
        found=1
        process_file "$pdf" "ocr"
    fi
done

# Scansnap-OCRed: already has text layer
for pdf in "$MONITOR_DIR_OCRED"/*.pdf "$MONITOR_DIR_OCRED"/*.PDF; do
    if [ -f "$pdf" ]; then
        found=1
        process_file "$pdf" "skip-ocr"
    fi
done

if [ "$found" -eq 0 ]; then
    log "Triggered but no PDFs found."
fi

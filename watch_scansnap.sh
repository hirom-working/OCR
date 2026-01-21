#!/bin/bash
#
# Scansnap Folder Watcher - fswatch version
# Monitors ~/Scansnap for new PDF files and processes them via OCR pipeline
#

MONITOR_DIR="$HOME/Scansnap"
SCRIPT_DIR="/Users/hirom/Projects/OCR"
PYTHON="$SCRIPT_DIR/.venv/bin/python"
PROCESSOR="$SCRIPT_DIR/process_pdf.py"
LOG_FILE="$HOME/Library/Logs/scansnap_watcher.log"
PID_FILE="/tmp/scansnap_watcher.pid"
LOCK_DIR="/tmp/scansnap_watcher_locks"

# Create directories if needed
mkdir -p "$(dirname "$LOG_FILE")"
mkdir -p "$MONITOR_DIR"
mkdir -p "$LOCK_DIR"

log() {
    echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE"
}

cleanup() {
    log "Shutting down..."
    rm -f "$PID_FILE"
    rm -rf "$LOCK_DIR"
    exit 0
}

trap cleanup SIGINT SIGTERM

# Check dependencies
if ! command -v fswatch &> /dev/null; then
    log "ERROR: fswatch not found. Install with: brew install fswatch"
    exit 1
fi

if [ ! -f "$PYTHON" ]; then
    log "ERROR: Python not found at $PYTHON"
    exit 1
fi

# Save PID
echo $$ > "$PID_FILE"

log "Starting Scansnap watcher..."
log "Monitoring: $MONITOR_DIR"
log "Log file: $LOG_FILE"

# Wait for file to stabilize (size stops changing)
wait_for_stable() {
    local file="$1"
    local prev_size=-1
    local curr_size

    sleep 2  # Initial wait

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
process_file() {
    local file="$1"
    local filename
    filename=$(basename "$file")

    # Ignore hidden files, temp files, and non-PDFs
    if [[ "$filename" == .* ]] || [[ "$filename" == ~* ]]; then
        return
    fi

    if [[ ! "$filename" =~ \.[pP][dD][fF]$ ]]; then
        return
    fi

    # Atomic lock using mkdir (prevents race condition)
    local lock_file="$LOCK_DIR/$filename.lock"
    if ! mkdir "$lock_file" 2>/dev/null; then
        # Already processing this file
        return
    fi

    log "Detected: $filename"

    # Wait for file to be fully written
    if ! wait_for_stable "$file"; then
        log "  File disappeared: $filename"
        rm -rf "$lock_file"
        return
    fi

    log "  Processing..."
    if "$PYTHON" "$PROCESSOR" "$file" >> "$LOG_FILE" 2>&1; then
        log "  Completed: $filename"
    else
        log "  ERROR: Failed to process $filename"
    fi

    # Remove lock file
    rm -rf "$lock_file"
}

# Process existing files first
log "Checking for existing PDFs..."
for pdf in "$MONITOR_DIR"/*.pdf "$MONITOR_DIR"/*.PDF; do
    if [ -f "$pdf" ]; then
        process_file "$pdf"
    fi
done

# Start watching with fswatch (FSEvents monitor for macOS)
log "Watching for new files..."
fswatch -0 --event Created "$MONITOR_DIR" | while IFS= read -r -d '' file; do
    process_file "$file"
done

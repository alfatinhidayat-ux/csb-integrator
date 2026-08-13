#!/usr/bin/env bash
# =============================================================================
# PANTUA MIGRASI BRIGHTER -> CSB (realtime)
#
# Penggunaan:
#   ./pantau_migrasi.sh                 # sekali tampil
#   ./pantau_migrasi.sh --watch 10      # auto-refresh tiap 10 detik
#   ./pantau_migrasi.sh --log          # tail -f log migrasi aktif
#   ./pantau_migrasi.sh --db           # tampil query DB yang sedang berjalan
#
# Kredensial SSH diambil dari env. Contoh:
#   export CSB_SSH_HOST=31.97.67.49
#   export CSB_SSH_USER=root
#   export CSB_SSH_PASS="password-anda"
# =============================================================================

SSH_HOST="${CSB_SSH_HOST:-31.97.67.49}"
SSH_USER="${CSB_SSH_USER:-root}"
SSH_PASS="${CSB_SSH_PASS:?Set CSB_SSH_PASS (atau jalankan via ./pantau_migrasi.sh dengan SSH_PASS) }"

LOG_DIR=/home/csb-integrator/logs
BASE_DIR=/home/csb-integrator

_ssh() {
    sshpass -p "$SSH_PASS" ssh -o StrictHostKeyChecking=accept-new \
        -o ConnectTimeout=10 "$SSH_USER@$SSH_HOST" "$1"
}

show_processes() {
    echo "── PROSES MIGRASI BERJALAN ────────────────────────────────"
    _ssh "ps aux | grep -E 'run_migration|run_sequence|reconcile|sync_dash|refresh|catchup' | grep -v grep | awk '{printf \"%-8s %6s %-9s %s\\n\", \$2, \$9, \$11, \$12\" \"\$13\" \"\$14}' || echo '(tidak ada)'"
}

show_last_log() {
    echo ""
    echo "── LOG AKTIF (terbaru per kategori) ────────────────────────"
    _ssh "for p in run_migration run_sequence sequence_serial reconcile normalize migrate; do f=\$(ls -t $LOG_DIR/*${p}* 2>/dev/null | head -1); [ -n \"\$f\" ] && echo \"\$f\"; done | head -12"
}

show_sequence_status() {
    echo ""
    echo "── STATUS SEQUENCE (log terakhir) ──────────────────────────"
    _ssh "tail -6 $LOG_DIR/sequence_serial.log 2>/dev/null"
}

show_db_queries() {
    echo ""
    echo "── QUERY DB SEDANG BERJALAN (>5 detik) ────────────────────"
    DB_SQL="SELECT id,user,host,command,time,LEFT(state,25) st,LEFT(info,70) info FROM information_schema.processlist WHERE command NOT IN ('Sleep','Daemon') ORDER BY time DESC"
    DB_HOST="${CSB_DB_HOST:-31.97.67.49}"
    DB_PORT="${CSB_DB_PORT:-3306}"
    DB_USER="${CSB_DB_USER:-admin}"
    DB_PASS="${CSB_DB_PASS:?Set CSB_DB_PASS untuk cek query DB}"
    DB_NAME="${CSB_DB_NAME:-csb_db}"
    mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASS" -N -e "$DB_SQL" "$DB_NAME" 2>/dev/null | awk -F'\t' '{printf "%-6s %-8s %-16s %-9s %5ss %-25s %.60s\n", $1, $2, $3, $4, $5, $6, $7}' || echo "(gagal konek DB atau mysql CLI tidak ada)"
}

show_konfig_dash() {
    echo ""
    echo "── DASH_PENERIMAAN_PER_USER (cek apakah re-sync diperlukan) ─"
    DB_HOST="${CSB_DB_HOST:-31.97.67.49}"
    DB_PORT="${CSB_DB_PORT:-3306}"
    DB_USER="${CSB_DB_USER:-admin}"
    DB_PASS="${CSB_DB_PASS:-$SSH_PASS}"
    DB_NAME="${CSB_DB_NAME:-csb_db}"
    mysql -h "$DB_HOST" -P "$DB_PORT" -u "$DB_USER" -p"$DB_PASS" -N -e \
        "SELECT cabang_id, bulan, MIN(synced_at), MAX(synced_at) FROM dash_penerimaan_per_user WHERE bulan IN ('2026-05-01','2026-06-01','2026-07-01') GROUP BY cabang_id, bulan ORDER BY cabang_id, bulan" \
        "$DB_NAME" 2>/dev/null | awk -F'\t' '{printf "cb%-3s %-12s sync:%s..%s\n", $1, $2, $3, $4}' || echo "(lewat — butuh CSB_DB_PASS)"
}

usage() {
    sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
}

# ---------------------------------------------------------------------------
case "$1" in
    --watch)
        INTERVAL="${2:-10}"
        while true; do
            clear
            date "+%Y-%m-%d %H:%M:%S"
            show_processes
            show_sequence_status
            show_last_log
            show_db_queries
            echo ""
            echo "(auto-refresh tiap ${INTERVAL}s — Ctrl+C untuk stop)"
            sleep "$INTERVAL"
        done
        ;;
    --log)
        F=$( _ssh "ls -t $LOG_DIR/*.log $LOG_DIR/*.out 2>/dev/null | head -1" )
        echo "Tail -f: $F"
        _ssh "tail -f \"$F\""
        ;;
    --db)
        show_db_queries
        ;;
    --sync)
        show_konfig_dash
        ;;
    *)
        clear
        date "+%Y-%m-%d %H:%M:%S"
        show_processes
        show_sequence_status
        show_last_log
        show_db_queries
        echo ""
        echo "Gunakan: $0 --watch [detik]  |  --log  |  --db  |  --sync"
        ;;
esac

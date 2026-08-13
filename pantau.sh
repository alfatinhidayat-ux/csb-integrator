#!/bin/bash
# Pantau migrasi Brighter->Clarify 2026 (April, Mei, Juni, Juli).
#
# Cara pakai:
#   ./pantau.sh              -> ringkasan status semua migrasi + tail terakhir tiap log
#   ./pantau.sh follow mei   -> ikuti log Mei secara live (mei|juni|juli|april)
#   ./pantau.sh april        -> tail 30 baris terakhir bulan tsb (tanpa nempel)
#
DIR=/home/csb-integrator/logs
APRIL=$(ls -t $DIR/run_migration_feb_apr_*.log 2>/dev/null | head -1)

pilih_log() {
  case "$1" in
    april) echo "$APRIL" ;;
    mei)   echo "$DIR/run_migration_2026-05.log" ;;
    juni)  echo "$DIR/run_migration_2026-06.log" ;;
    juli)  echo "$DIR/run_migration_2026-07.log" ;;
    *)     echo "" ;;
  esac
}

follow() {
  local f=$(pilih_log "$1")
  if [ -z "$f" ] || [ ! -f "$f" ]; then
    echo "Log bulan '$1' belum ada. Status global:"
    cat $DIR/sequence_may_jun_jul.log 2>/dev/null || echo "(belum ada catatan)"
    echo
    echo "Isi direktori log:"
    ls -lt $DIR | head
    return 1
  fi
  echo "Mengikuti: $f  (Ctrl+C untuk keluar)"
  tail -f "$f"
}

ringkas() {
  echo "================ PROSES MIGRASI BERJALAN ================"
  ps -ef | grep -v grep | grep -E "run_migration_feb_apr|run_sequence_may_jun_jul" \
    | awk '{print "PID "$2" | "$8" "$9" "$10" "$11" "$12" "$13" "$14}'
  [ $? -eq 1 ] && echo "(tidak ada proses migrasi aktif)"

  echo
  echo "================ STATUS GLOBAL (sequence) ================"
  tail -8 $DIR/sequence_may_jun_jul.log 2>/dev/null || echo "(belum ada)"

  echo
  echo "================ TAIL TERAKHIR PER BULAN ================"
  for bln in april mei juni juli; do
    f=$(pilih_log $bln)
    if [ -f "$f" ]; then
      echo "----- $bln : $(basename $f) -----"
      tail -4 "$f"
      echo
    else
      echo "----- $bln : (belum ada log) -----"
      echo
    fi
  done
}

case "$1" in
  follow) follow "$2" ;;
  "")     ringkas ;;
  *)      f=$(pilih_log "$1")
          if [ -n "$f" ] && [ -f "$f" ]; then
            tail -30 "$f"
          else
            ringkas
          fi ;;
esac

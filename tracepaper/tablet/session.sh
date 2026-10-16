# A viewer's session on the reMarkable: tracepaper/pen.py runs this over ssh while the
# viewer has the tablet, with these set on a line before it:
#   owner     the viewer, so that it can take over its own session after a dropped connection
#   take      1 to take the tablet over from another viewer that has it
#   screen    1 to show the view with program (tablet/main.cpp), turned by turn degrees
#   program   tracepaper-screen on the tablet
#   status_taken, status_busy, status_left
#             exit statuses for: another viewer took the tablet over / has it; the person
#             disconnected with the screen's button
#
# It stops the tablet's app, shows the screen, and streams the pen's input events after a
# line saying it is ready. It starts the app again when its input ends, when no heartbeat
# came for $silence seconds, or when the screen's Disconnect button signals it (USR2). A
# session taken over (USR1) leaves the app stopped for the one taking over.
#
# Its input carries heartbeats (empty lines) and pictures for the screen: a line
# "view <bytes> <done>" and the PNG, which program takes out of the input.

: "${lock:=/tmp/tracepaper-pen}"          # "<pid> <owner>" of the session that has the tablet
: "${view:=/tmp/tracepaper-view.png}"     # the picture the screen shows
: "${silence:=20}"
: "${devices:=/proc/bus/input/devices}"
: "${input:=/dev/input}"

dev=$(grep -A4 -i wacom "$devices" | grep -o 'event[0-9]*' | head -n 1)
[ -n "$dev" ] || { echo "no pen sensor found on the tablet" >&2; exit 1; }

# Another session has the tablet: take it over if asked to, or if it is this viewer's own.
if [ -f "$lock" ] && read -r old who < "$lock" && grep -q TRACEPAPER "/proc/$old/cmdline" 2>/dev/null; then
  [ "$take" = 1 ] || [ "$who" = "$owner" ] || { echo "in use by another viewer" >&2; exit "$status_busy"; }
  kill -USR1 "$old"
  n=0
  while kill -0 "$old" 2>/dev/null && [ $n -lt 100 ]; do sleep 0.05; n=$((n + 1)); done
  kill -9 "$old" 2>/dev/null
fi
echo "$$ $owner" > "$lock"

# Only the session's own shell cleans up: a background job killed before it has started
# its program is still a copy of this shell, with these traps.
main() { [ "${BASHPID:-$$}" = "$$" ]; }
restore() {
  main || exit 0
  trap - EXIT HUP INT TERM PIPE USR1 USR2
  kill $pid $shown 2>/dev/null
  wait $shown
  rm -f "$lock" "$view" "$view.done"
  systemctl reset-failed xochitl 2>/dev/null
  systemctl start xochitl
}
taken() {
  main || exit 0
  trap - EXIT HUP INT TERM PIPE USR1 USR2
  kill $pid $shown 2>/dev/null
  wait $shown
  echo "taken by another viewer" >&2
  exit "$status_taken"
}
trap 'restore; exit' EXIT HUP INT TERM PIPE
trap taken USR1
trap 'restore; echo "disconnected on the tablet" >&2; exit "$status_left"' USR2

systemctl stop xochitl || exit 1
rm -f "$view" "$view.done"
if [ "$screen" = 1 ]; then
  QT_QPA_PLATFORM=epaper QT_QUICK_BACKEND=epaper QT_QPA_EVDEV_TOUCHSCREEN_PARAMETERS=rotate=180:invertx \
    "$program" "$view" "$turn" "$input/$dev" >/dev/null 2>&1 &
  shown=$!
fi
printf 'TRACEPAPER-READY\n'  # what tracepaper/pen.py waits for
cat "$input/$dev" &
pid=$!
while read -t "$silence" cmd size done; do
  if [ "$cmd" = view ]; then "$program" --receive "$view" "$size" "$done"; fi
done

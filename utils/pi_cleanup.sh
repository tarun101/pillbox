#!/usr/bin/env bash
# Remove stray dev-iteration scripts and one-off test images left loose in the
# Pi home directory. These predate pillbox_app.py and the utils/ scripts and
# are not used by the running app.
#
# It NEVER touches your data or secrets: photos/, pillbox_app.py, ~/.pillbox_pin,
# ~/.cloudflared, ~/.config, ~/.ssh, login_url.txt, or any dotfile — only the
# specific loose files listed below.
#
# Usage:
#   bash pi_cleanup.sh          # list what would go, then ask to confirm
#   bash pi_cleanup.sh --yes    # delete without prompting
set -euo pipefail

# Superseded standalone scripts (the current app is pillbox_app.py; the test
# streams/captures now live in this repo's utils/).
scripts=(
  camera.py
  camera_test.py
  camera_stream.py
  camera_stream_full.py
  camera_stream_full_pi5.py
  capture_full_res.py
  FINAL_RasPi_Code.py
)

# One-off test captures and crops.
images=(
  photo.jpg
  pill_picture.jpg
  slot_r1_c1.jpg
  test.jpg
  test_check.jpg
  test_full_res.jpg
)

targets=()
for f in "${scripts[@]}" "${images[@]}"; do
  [ -e "$HOME/$f" ] && targets+=("$f")
done

if [ ${#targets[@]} -eq 0 ]; then
  echo "Nothing to clean — home is already tidy."
  exit 0
fi

echo "The following stray files in $HOME will be removed:"
printf '  %s\n' "${targets[@]}"
echo
echo "Left untouched: photos/, pillbox_app.py, ~/.pillbox_pin, ~/.cloudflared,"
echo "                ~/.config, ~/.ssh, login_url.txt, and all dotfiles."
echo

if [ "${1:-}" != "--yes" ]; then
  read -r -p "Delete these ${#targets[@]} files? [y/N] " reply
  case "$reply" in
    y|Y|yes|Yes) ;;
    *) echo "Aborted — nothing deleted."; exit 0 ;;
  esac
fi

for f in "${targets[@]}"; do
  rm -f -- "$HOME/$f"
  echo "removed $f"
done
echo "Done."

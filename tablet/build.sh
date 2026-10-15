#!/bin/sh
# Builds tracepaper-screen for the reMarkable 2 into tracepaper/tablet/, from where the
# viewer copies it onto the tablet. Needs reMarkable's SDK for the tablet's software
# version (https://developer.remarkable.com/links), installed as their guide does:
#   sh remarkable-production-image-*-rm2-public-x86_64-toolchain.sh -d ~/codex-sdk/rm2/<version>
# Usage: tablet/build.sh [SDK directory]  (default: the newest in ~/codex-sdk/rm2)
set -e
here=$(cd "$(dirname "$0")" && pwd)
sdk=${1:-$(ls -d "$HOME"/codex-sdk/rm2/*/ | sort -V | tail -n 1)}
out="$(dirname "$here")/tracepaper/tablet/tracepaper-screen"
build=$(mktemp -d)
trap 'rm -rf "$build"' EXIT
# The SDK refuses to work with LD_LIBRARY_PATH set (for CUDA, say), so leave it out here.
env -u LD_LIBRARY_PATH sh -c '
  . "$1"/environment-setup-cortexa7hf-neon-remarkable-linux-gnueabi
  cmake -S "$2" -B "$3" -DCMAKE_BUILD_TYPE=MinSizeRel >/dev/null
  cmake --build "$3" >/dev/null
  $STRIP -o "$4" "$3/tracepaper-screen"
' sh "$sdk" "$here" "$build" "$out"
echo "built $out with $(basename "$sdk")"

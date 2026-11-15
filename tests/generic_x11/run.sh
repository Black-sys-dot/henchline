#!/bin/sh
# Prove the desktop interface on another desktop and distribution, without touching this one:
# a throwaway Fedora container (no KDE, no Klipper, no Konsole), the repository mounted read-only.
set -eu
here=$(CDPATH= cd -- "$(dirname "$0")/../.." && pwd)
exec docker run --rm -v "$here:/repo:ro" fedora:41 sh /repo/tests/generic_x11/inside.sh

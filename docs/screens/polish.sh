#!/bin/sh
# Phone screenshots for the README, made tidy: the status bar (clock, other apps' notifications) and the
# navigation bar cut off, rounded corners, a soft shadow. Needs ImageMagick.
#
#   docs/screens/polish.sh RAW.png OUT.png      (RAW from: adb exec-out screencap -p > RAW.png)
set -eu
raw=$1 out=$2
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
w=$(identify -format %w "$raw") h=$(identify -format %h "$raw")
top=$((h * 58 / 1000)) bottom=$((h * 22 / 1000))   # status bar and gesture bar, as a share of the height
convert "$raw" -crop "${w}x$((h - top - bottom))+0+$top" +repage -resize 600x "$tmp/screen.png"
sw=$(identify -format %w "$tmp/screen.png") sh=$(identify -format %h "$tmp/screen.png")
convert -size "${sw}x${sh}" xc:none -fill white -draw "roundrectangle 0,0 $((sw - 1)),$((sh - 1)) 34,34" "$tmp/mask.png"
convert "$tmp/screen.png" -alpha set "$tmp/mask.png" -compose DstIn -composite "$tmp/round.png"
convert "$tmp/round.png" \( +clone -background '#0A1430' -shadow 30x12+0+8 \) +swap -background none \
  -layers merge +repage -define png:color-type=6 "$out"

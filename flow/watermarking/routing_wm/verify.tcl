# SPDX-License-Identifier: BSD-3-Clause
# Check the placement and CTS watermarks in a finished design.
#
# Driven by the watermark_verify make target, which supplies WM_VERIFY_ODB,
# WM_RESULTS and WM_TAU.  Claim files that were never written -- a stage with
# no capacity on this design, for instance -- are simply not passed, and
# verify_watermark reports on whichever stages it was given.

proc wm_env_or { name fallback } {
  if { [info exists ::env($name)] && $::env($name) ne "" } {
    return $::env($name)
  }
  return $fallback
}

set odb [wm_env_or WM_VERIFY_ODB ""]
if { $odb eq "" || ![file exists $odb] } {
  utl::error WMK 110 "WM_VERIFY_ODB is unset or does not exist: '$odb'."
}
read_db $odb

set results [wm_env_or WM_RESULTS "."]
set tau [wm_env_or WM_TAU 0.75]

set args {}
foreach { opt file } [list \
  -placement_claims $results/wm_place_order_embed.csv \
  -cts_claims $results/wm_cts_pairs_embed.csv \
] {
  if { [file exists $file] } {
    lappend args $opt $file
  } else {
    utl::warn WMK 111 "No claims for [string range $opt 1 end]: $file"
  }
}

if { [llength $args] == 0 } {
  utl::error WMK 112 "No claim files found under $results; nothing to verify."
}

if { ![verify_watermark {*}$args -tau $tau] } {
  utl::error WMK 113 "Ownership evidence does not hold."
}

# SPDX-License-Identifier: BSD-3-Clause
#
# PROTOTYPE: surgical routing attack -- reroute ONLY the watermark nets while
# preserving the rest of the stolen detailed routing.
#
# Mechanism (uses OpenROAD's fixed-net primitive; see OpenROAD io.cpp:646-692:
# a net whose routing wire-type is FIXED is loaded with frNet::setFixed(true)
# and is NOT rerouted by TritonRoute, while an *unrouted* net IS routed):
#   1. read the fully-routed watermarked ODB (5_route.odb)
#   2. for every ordinary signal net NOT in the attack list: setWireType FIXED
#      -> DRT keeps its routing verbatim (obstacle only)
#   3. for every net IN the attack list: destroyNetWires (unroute) and destroy
#      its "watermark" dbBoolProperty -> DRT reroutes it WITHOUT the wrong-way
#      bias, erasing the routing watermark on exactly those nets
#   4. global_route (regenerate guides) + detailed_route -> only the unrouted
#      attack nets are (re)routed; everything else is preserved
#   5. write the surgically-rerouted ODB
#
# Env:
#   WM_ODB           input fully-routed watermarked ODB
#   WM_NETS_ATTACK   text file, one net name per line (the watermark subset)
#   WM_OUT_ODB       output ODB
#
# NOTE: prototype -- needs one validation run.  If global_route disturbs the
# FIXED nets' guides, switch to the incremental-GRT path (grt::start_incremental
# / end_incremental) marking only the unrouted nets dirty.

proc _env {k {d ""}} { return [expr {[info exists ::env($k)] ? $::env($k) : $d}] }

read_db [_env WM_ODB]
set block [ord::get_db_block]

# --- load attack net names ---------------------------------------------------
set atk [dict create]
set fh [open [_env WM_NETS_ATTACK] r]
while {[gets $fh line] >= 0} {
  set n [string trim $line]
  if {$n ne ""} { dict set atk $n 1 }
}
close $fh

# --- fix the stolen routing; unroute + de-tag the attack nets ----------------
# Only reroute nets that are BOTH in the attack subset AND carry the routing
# watermark ("watermark" dbBoolProperty).  Everything else -- including
# non-watermark nets that happen to be in the random subset -- is preserved
# FIXED, so we touch only the marked nets (truly surgical).
set n_fixed 0
set n_reroute 0
foreach net [$block getNets] {
  if {[$net isSpecial]} { continue }
  set nm [$net getName]
  set wire [$net getWire]
  set prop [odb::dbBoolProperty_find $net "watermark"]
  set is_wm [expr {$prop ne "NULL" && $prop ne ""}]
  if {[dict exists $atk $nm] && $is_wm} {
    if {$wire ne "NULL" && $wire ne ""} { odb::dbWire_destroy $wire }
    odb::dbProperty_destroy $prop
    incr n_reroute
  } else {
    if {$wire ne "NULL" && $wire ne ""} { $net setWireType FIXED; incr n_fixed }
  }
}
puts "surgical_reroute: reroute(watermark nets)=$n_reroute preserved_fixed=$n_fixed"

# --- regenerate guides, then detailed-route only the unrouted attack nets ----
global_route
detailed_route -verbose 1

write_db [_env WM_OUT_ODB]
puts "surgical_reroute: wrote [_env WM_OUT_ODB]"

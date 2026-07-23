# SPDX-License-Identifier: BSD-3-Clause
#
# Matched-thread reroute timing experiment: surgical (watermark nets only) vs
# full (all nets), both starting ONLY from the fully-routed watermarked ODB
# (the attacker has no other files), so read_db + pin-access + grid build are
# paid identically in both.
#
# Env:
#   WM_ODB      input fully-routed watermarked ODB (attacker's only input)
#   WM_OUT_ODB  output ODB (written in both modes -- same cost)
#   MODE        "surgical" (reroute watermark nets) | "full" (reroute all)
#   WM_FRAC     keyed fraction for platforms with NO routing watermark (ASAP7)
#   DRY         "1" -> stop after net selection (validate load+selection only)
#
# Thread count is set on the openroad command line (-threads N), identical for
# both modes.

proc _env {k {d ""}} { return [expr {[info exists ::env($k)] ? $::env($k) : $d}] }

set MODE [_env MODE surgical]
set FRAC [expr {double([_env WM_FRAC 0.02])}]
set DRY  [_env DRY 0]

# FNV-1a string hash -> [0,1); keyed net selection when no watermark tags exist
proc _hfrac {s} {
  set h 2166136261
  binary scan $s c* bytes
  foreach b $bytes {
    set h [expr {(($h ^ ($b & 0xff)) * 16777619) & 0xffffffff}]
  }
  return [expr {$h / 4294967296.0}]
}

puts "reroute_experiment: reading [_env WM_ODB]"
read_db [_env WM_ODB]
set block [ord::get_db_block]

# pass 1: does this layout carry routing-watermark tags?
set n_tag 0
foreach net [$block getNets] {
  if {[$net isSpecial]} { continue }
  set prop [odb::dbBoolProperty_find $net "watermark"]
  if {$prop ne "NULL" && $prop ne ""} { incr n_tag }
}
puts "reroute_experiment: MODE=$MODE watermark_tagged_nets=$n_tag frac=$FRAC"

# pass 2: unroute the reroute-set, FIX everything else
# WM_CLEAR_TAG=1 (default): strip the "watermark" dbBoolProperty from rerouted
# nets so the watermark-aware TritonRoute does NOT re-apply the wrong-way penalty
# at mazeNetInit (otherwise the reroute simply re-embeds the mark).  Set to 0 to
# reproduce the buggy "tag left in place" behaviour.
set CLEAR [_env WM_CLEAR_TAG 1]

set n_reroute 0
set n_fixed 0
set n_cleared 0
set reroute_names [list]
foreach net [$block getNets] {
  if {[$net isSpecial]} { continue }
  set nm [$net getName]
  set wire [$net getWire]
  set prop [odb::dbBoolProperty_find $net "watermark"]
  set is_wm [expr {$prop ne "NULL" && $prop ne ""}]
  set do 0
  if {$MODE eq "full"} {
    set do 1
  } elseif {$n_tag > 0} {
    set do $is_wm
  } else {
    set do [expr {[_hfrac $nm] < $FRAC}]
  }
  if {$do} {
    if {$wire ne "NULL" && $wire ne ""} { odb::dbWire_destroy $wire }
    if {$is_wm && $CLEAR eq "1"} { odb::dbProperty_destroy $prop; incr n_cleared }
    lappend reroute_names $nm
    incr n_reroute
  } else {
    if {$wire ne "NULL" && $wire ne ""} { $net setWireType FIXED; incr n_fixed }
  }
}
puts "reroute_experiment: reroute_nets=$n_reroute fixed_nets=$n_fixed tags_cleared=$n_cleared"

# dump the reroute-net set (= watermark set WM_R when tags exist)
if {[_env WM_NETS_OUT ""] ne "" && $MODE ne "full"} {
  set oh [open [_env WM_NETS_OUT] w]
  foreach nm $reroute_names { puts $oh $nm }
  close $oh
  puts "reroute_experiment: wrote watermark-net list -> [_env WM_NETS_OUT]"
}

if {$DRY eq "1"} {
  puts "reroute_experiment: DRY run -- stopping before global_route"
  exit 0
}

# regenerate guides for the unrouted nets, then detail-route them
global_route
detailed_route -verbose 1

write_db [_env WM_OUT_ODB]
puts "reroute_experiment: DONE mode=$MODE reroute_nets=$n_reroute"

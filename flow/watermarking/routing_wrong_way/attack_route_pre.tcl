# SPDX-License-Identifier: BSD-3-Clause
#
# Pre-DRT hook for the blind/targeted routing attack (paper §7.1).
#
# Algorithm:
#   1. Tag every WM-selected net normally via set_routing_watermark -key_hex
#      (so the embedder's selection rule is honored).
#   2. Read the attack-net list from WM_NETS_ATTACK (one net name per line).
#   3. For each net in that list, destroy its dbBoolProperty "watermark"
#      so DRT will route it WITHOUT the wrong-way bias.
#
# This faithfully implements the paper's "locally reroutes the selected nets
# without the watermark wrong-way penalty" attack, modulo OpenROAD's lack of
# a per-net rip-up primitive (we re-run detail_route on the whole design).

set wm_fraction [env_var_or_empty WATERMARK_FRACTION]
if { $wm_fraction eq "" } { set wm_fraction 0.05 }

set wm_strength [env_var_or_empty WATERMARK_STRENGTH]
if { $wm_strength ne "" } {
  set_routing_watermark_strength $wm_strength
}

set seed_path [env_var_or_empty WM_SEED_HEX]
set used_key 0
if { $seed_path ne "" && [file exists $seed_path] } {
  set fp [open $seed_path r]
  set seed_hex [string trim [read $fp]]
  close $fp
  regsub -all {[^0-9a-fA-F]} $seed_hex "" seed_hex
  if { [string length $seed_hex] == 64 } {
    set_routing_watermark -key_hex $seed_hex -fraction $wm_fraction
    set used_key 1
  } else {
    puts "attack_route_pre WARN: WM_SEED_HEX is not 64 hex chars; tagging skipped."
  }
}

if { !$used_key } {
  set wm_message [env_var_or_empty WATERMARK_MESSAGE]
  if { $wm_message ne "" } {
    set_routing_watermark -message $wm_message -fraction $wm_fraction
    set used_key 1
  }
}

# Now drop the watermark tag on every attacker-chosen net.
set atk_file [env_var_or_empty WM_NETS_ATTACK]
set n_total 0
set n_cleared 0
if { $atk_file ne "" && [file exists $atk_file] } {
  set db    [ord::get_db]
  set block [[$db getChip] getBlock]
  set fh [open $atk_file r]
  while { [gets $fh line] >= 0 } {
    set name [string trim $line]
    if { $name eq "" } { continue }
    incr n_total
    set net [$block findNet $name]
    if { $net eq "NULL" || $net eq "" } { continue }
    set prop [odb::dbBoolProperty_find $net "watermark"]
    if { $prop eq "NULL" || $prop eq "" } { continue }
    # `odb::dbProperty_destroy` is the SWIG-bound static destroy on the base
    # dbProperty class (see OpenROAD/src/odb/include/odb/db.h:8972).  The
    # subclass-specific `dbBoolProperty_destroy` does NOT exist.
    odb::dbProperty_destroy $prop
    incr n_cleared
  }
  close $fh
}

# NOTE: square brackets `[...]` in a double-quoted Tcl string trigger command
# substitution, so we use a bare prefix here (no [...]) to avoid Tcl trying
# to invoke a command named "attack_route_pre".
puts "attack_route_pre: used_key=$used_key fraction=$wm_fraction\
      atk_file=$atk_file requested=$n_total cleared=$n_cleared"

# Dump the resulting watermark-net list (post-attack) so downstream
# verification can inspect what DRT will actually see.
set db    [ord::get_db]
set block [[$db getChip] getBlock]
set out_path "$::env(RESULTS_DIR)/watermark_nets_attacked.txt"
set f [open $out_path w]
foreach net [$block getNets] {
  if { [odb::dbBoolProperty_find $net "watermark"] ne "NULL" } {
    puts $f [$net getName]
  }
}
close $f

DESIGN=bp_multi_top PLATFORM=nangate45 WM_FLOW_VARIANT=base_tcp3p2 bash drivers/run_p_only.sh       # placement only
DESIGN=bp_multi_top PLATFORM=nangate45 WM_FLOW_VARIANT=base_tcp3p2 bash drivers/run_c_only.sh       # CTS only
DESIGN=bp_multi_top PLATFORM=nangate45 WM_FLOW_VARIANT=base_tcp3p2 bash drivers/run_r_only.sh       # routing only
DESIGN=bp_multi_top PLATFORM=nangate45 WM_FLOW_VARIANT=base_tcp3p2 bash drivers/run_all_stage.sh    # P → C → R chained (all-stage)
DESIGN=ariane PLATFORM=asap7 WM_FLOW_VARIANT=base_fixed bash drivers/run_p_only.sh       # placement only
DESIGN=ariane PLATFORM=asap7 WM_FLOW_VARIANT=base_fixed bash drivers/run_c_only.sh       # CTS only
DESIGN=ariane PLATFORM=asap7 WM_FLOW_VARIANT=base_fixed bash drivers/run_r_only.sh       # routing only
DESIGN=ariane PLATFORM=asap7 WM_FLOW_VARIANT=base_fixed bash drivers/run_all_stage.sh    # P → C → R chained (all-stage)

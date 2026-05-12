# DESIGN=aes PLATFORM=nangate45 WM_FLOW_VARIANT=watermarking-test1 bash drivers/run_p_only.sh       # placement only

DESIGN=aes PLATFORM=nangate45 WM_FLOW_VARIANT=watermarking-test1 bash drivers/run_c_only.sh       # CTS only

# DESIGN=aes PLATFORM=nangate45 WM_FLOW_VARIANT=watermarking-test1 bash drivers/run_r_only.sh       # routing only

# DESIGN=aes PLATFORM=nangate45 WM_FLOW_VARIANT=watermarking-test1 bash drivers/run_all_stage.sh    # P → C → R chained (all-stage)
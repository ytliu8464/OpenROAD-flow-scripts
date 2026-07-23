export PLATFORM               = asap7
export DESIGN_NAME            = ariane
export DESIGN_NICKNAME 	      = ariane136

export SRC_HOME = $(DESIGN_HOME)/src/$(DESIGN_NAME)

# Package files must be listed first
export VERILOG_FILES          = $(SRC_HOME)/rtl/dm_pkg.sv \
	$(SRC_HOME)/rtl/riscv_pkg.sv \
	$(SRC_HOME)/rtl/axi_pkg.sv \
	$(SRC_HOME)/rtl/ariane_pkg.sv \
	$(SRC_HOME)/rtl/ariane_axi_pkg.sv \
	$(SRC_HOME)/rtl/instruction_tracer_pkg.sv \
	$(SRC_HOME)/rtl/serpent_cache_pkg.sv \
	$(SRC_HOME)/rtl/std_cache_pkg.sv \
	$(SRC_HOME)/rtl/alu.sv \
	$(SRC_HOME)/rtl/amo_buffer.sv \
	$(SRC_HOME)/rtl/apb_to_reg.sv \
	$(SRC_HOME)/rtl/ariane_regfile_ff.sv \
	$(SRC_HOME)/rtl/ariane.sv \
	$(SRC_HOME)/rtl/axi2apb_64_32.sv \
	$(SRC_HOME)/rtl/axi2apb.sv \
	$(SRC_HOME)/rtl/axi2apb_wrap.sv \
	$(SRC_HOME)/rtl/axi2mem.sv \
	$(SRC_HOME)/rtl/axi_adapter.sv \
	$(SRC_HOME)/rtl/axi_ar_buffer.sv \
	$(SRC_HOME)/rtl/axi_aw_buffer.sv \
	$(SRC_HOME)/rtl/axi_b_buffer.sv \
	$(SRC_HOME)/rtl/axi_intf.sv \
	$(SRC_HOME)/rtl/axi_lite_interface.sv \
	$(SRC_HOME)/rtl/axi_master_connect_rev.sv \
	$(SRC_HOME)/rtl/axi_master_connect.sv \
	$(SRC_HOME)/rtl/axi_r_buffer.sv \
	$(SRC_HOME)/rtl/axi_single_slice.sv \
	$(SRC_HOME)/rtl/axi_slave_connect_rev.sv \
	$(SRC_HOME)/rtl/axi_slave_connect.sv \
	$(SRC_HOME)/rtl/axi_slice.sv \
	$(SRC_HOME)/rtl/axi_slice_wrap.sv \
	$(SRC_HOME)/rtl/axi_w_buffer.sv \
	$(SRC_HOME)/rtl/bht.sv \
	$(SRC_HOME)/rtl/bootrom.sv \
	$(SRC_HOME)/rtl/branch_unit.sv \
	$(SRC_HOME)/rtl/btb.sv \
	$(SRC_HOME)/rtl/cdc_2phase.sv \
	$(SRC_HOME)/rtl/clint.sv \
	$(SRC_HOME)/rtl/cluster_clock_inverter.sv \
	$(SRC_HOME)/rtl/commit_stage.sv \
	$(SRC_HOME)/rtl/compressed_decoder.sv \
	$(SRC_HOME)/rtl/controller.sv \
	$(SRC_HOME)/rtl/csr_buffer.sv \
	$(SRC_HOME)/rtl/csr_regfile.sv \
	$(SRC_HOME)/rtl/debug_rom.sv \
	$(SRC_HOME)/rtl/decoder.sv \
	$(SRC_HOME)/rtl/dm_csrs.sv \
	$(SRC_HOME)/rtl/dmi_cdc.sv \
	$(SRC_HOME)/rtl/dmi_jtag.sv \
	$(SRC_HOME)/rtl/dmi_jtag_tap.sv \
	$(SRC_HOME)/rtl/dm_mem.sv \
	$(SRC_HOME)/rtl/dm_sba.sv \
	$(SRC_HOME)/rtl/dm_top.sv \
	$(SRC_HOME)/rtl/ex_stage.sv \
	$(SRC_HOME)/rtl/fifo_v1.sv \
	$(SRC_HOME)/rtl/fifo_v2.sv \
	$(SRC_HOME)/rtl/fifo_v3.sv \
	$(SRC_HOME)/rtl/frontend.sv \
	$(SRC_HOME)/rtl/id_stage.sv \
	$(SRC_HOME)/rtl/instr_realigner.sv \
	$(SRC_HOME)/rtl/instr_scan.sv \
	$(SRC_HOME)/rtl/instruction_tracer_if.sv \
	$(SRC_HOME)/rtl/issue_read_operands.sv \
	$(SRC_HOME)/rtl/issue_stage.sv \
	$(SRC_HOME)/rtl/lfsr_8bit.sv \
	$(SRC_HOME)/rtl/load_store_unit.sv \
	$(SRC_HOME)/rtl/load_unit.sv \
	$(SRC_HOME)/rtl/mmu.sv \
	$(SRC_HOME)/rtl/multiplier.sv \
	$(SRC_HOME)/rtl/mult.sv \
	$(SRC_HOME)/rtl/perf_counters.sv \
	$(SRC_HOME)/rtl/pipe_reg_simple.sv \
	$(SRC_HOME)/rtl/plic_claim_complete_tracker.sv \
	$(SRC_HOME)/rtl/plic_comparator.sv \
	$(SRC_HOME)/rtl/plic_find_max.sv \
	$(SRC_HOME)/rtl/plic_gateway.sv \
	$(SRC_HOME)/rtl/plic_interface.sv \
	$(SRC_HOME)/rtl/plic.sv \
	$(SRC_HOME)/rtl/plic_target_slice.sv \
	$(SRC_HOME)/rtl/ptw.sv \
	$(SRC_HOME)/rtl/pulp_clock_mux2.sv \
	$(SRC_HOME)/rtl/ras.sv \
	$(SRC_HOME)/rtl/reg_intf.sv \
	$(SRC_HOME)/rtl/re_name.sv \
	$(SRC_HOME)/rtl/rrarbiter.sv \
	$(SRC_HOME)/rtl/rstgen_bypass.sv \
	$(SRC_HOME)/rtl/scoreboard.sv \
	$(SRC_HOME)/rtl/serdiv.sv \
	$(SRC_HOME)/rtl/serpent_cache_subsystem.sv \
	$(SRC_HOME)/rtl/serpent_dcache_ctrl.sv \
	$(SRC_HOME)/rtl/serpent_dcache_mem.sv \
	$(SRC_HOME)/rtl/serpent_dcache_missunit.sv \
	$(SRC_HOME)/rtl/serpent_dcache.sv \
	$(SRC_HOME)/rtl/serpent_dcache_wbuffer.sv \
	$(SRC_HOME)/rtl/serpent_icache.sv \
	$(SRC_HOME)/rtl/serpent_l15_adapter.sv \
	$(SRC_HOME)/rtl/serpent_peripherals.sv \
	$(SRC_HOME)/sram_asap7_ariane136.sv \
	$(SRC_HOME)/rtl/std_cache_subsystem.sv \
	$(SRC_HOME)/rtl/store_buffer.sv \
	$(SRC_HOME)/rtl/store_unit.sv \
	$(SRC_HOME)/rtl/sync_wedge.sv \
	$(SRC_HOME)/rtl/tlb.sv \
	$(SRC_HOME)/rtl/lzc.sv \
	$(SRC_HOME)/rtl/std_icache.sv \
	$(SRC_HOME)/rtl/std_nbdcache.sv \
	$(SRC_HOME)/rtl/stream_arbiter.sv \
	$(SRC_HOME)/rtl/stream_mux.sv \
	$(SRC_HOME)/rtl/stream_demux.sv \
	$(SRC_HOME)/rtl/cache_ctrl.sv \
	$(SRC_HOME)/rtl/miss_handler.sv \
	$(SRC_HOME)/rtl/amo_alu.sv \
	$(SRC_HOME)/rtl/tag_cmp.sv \
	$(PLATFORM_DIR)/verilog/sram_asap7_16x256_1rw.sv

export ADDITIONAL_LEFS = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NICKNAME)/sram_asap7_16x256_1rw.lef 

export ADDITIONAL_LIBS = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NICKNAME)/sram_asap7_16x256_1rw.lib 

export SDC_FILE        = $(DESIGN_HOME)/$(PLATFORM)/$(DESIGN_NICKNAME)/constraint_evo.sdc

export CORE_UTILIZATION       ?= 30
export CORE_MARGIN            = 2
export MACRO_PLACE_HALO       = 3 3
export CLK_PERIOD             ?= 1050
export ABC_CLOCK_PERIOD_IN_PS ?= $(CLK_PERIOD)
export PLACE_DENSITY_LB_ADDON   ?= 0.20

# For use with SYNTH_HIERARCHICAL
export SYNTH_HIERARCHICAL = 1
export SYNTH_MINIMUM_KEEP_SIZE ?= 40000
export SYNTH_HDL_FRONTEND = slang

export ASAP7_USE_VT = RVT LVT SLVT

export CTS_LIB_NAME = asap7sc7p5t_INVBUF_SLVT_FF_nldm_211120
# export GPL_TIMING_DRIVEN = 1 

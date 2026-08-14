# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026, The OpenROAD Authors
#
# PDMarks watermarking, enabled with WATERMARK=1.
#
# Embeds keyed ownership evidence at three stages of a normal flow run:
#
#   placement  3_5_place_dp.odb -> 3_6_place_wm.odb   keyed x-order of cell pairs
#   cts        4_1_cts.odb      -> 4_2_cts_wm.odb     leaf-buffer fanout parity
#   routing    tagged nets biased by the detailed router (wmk module)
#
# Placement and CTS are inserted as extra substeps because their embedders run
# under "openroad -python"; the do-step macro is Tcl-only.  Routing needs no new
# rule -- it rides the stock PRE_/POST_ step hooks.
#
# Everything here is inert unless WATERMARK=1.

ifeq ($(WATERMARK),1)

export WM_HOME    = $(FLOW_HOME)/watermarking
export WM_RESULTS = $(RESULTS_DIR)

# Key bundle.  gen_key needs Ed25519, so it runs under an interpreter with
# "cryptography" installed rather than PYTHON_EXE.
WM_GEN_KEY    = $(WM_HOME)/gen_key
WM_KEY_DIR    = $(WM_GEN_KEY)/out/$(DESIGN_NAME)
WM_SEED_P     = $(WM_KEY_DIR)/seed_placement.hex
WM_SEED_C     = $(WM_KEY_DIR)/seed_cts.hex
WM_SEED_R     = $(WM_KEY_DIR)/seed_routing.hex
WM_KEY_BUNDLE = $(WM_KEY_DIR)/bundle.json
WM_OWNER_ID  ?= pdmarks-owner
# gen_key derives Ed25519 keys, so it needs an interpreter with "cryptography";
# PYTHON_EXE is the flow's interpreter and generally does not have it.
export GEN_KEY_PYTHON ?= $(shell command -v python3)

# Routing hooks.  PRE_DETAIL_ROUTE_TCL is required, not optional: ORFS runs
# each stage in its own OpenROAD process, and set_routing_watermark_strength
# mutates in-memory router configuration that does not survive into the
# detail-route process.  Tagging at global route is enough for the tags
# themselves, which persist in the ODB as a dbBoolProperty.  The hook is
# idempotent, so sourcing it at both points is safe.
export PRE_GLOBAL_ROUTE_TCL  = $(WM_HOME)/routing_wm/pre_route_watermark.tcl
export PRE_DETAIL_ROUTE_TCL  = $(WM_HOME)/routing_wm/pre_route_watermark.tcl
export POST_DETAIL_ROUTE_TCL = $(WM_HOME)/routing_wm/post_route_watermark.tcl
export WM_SEED_HEX           = $(WM_SEED_R)

# The embedders receive liberty and constraints straight from the flow rather
# than rediscovering them.
#
# Every path is absolute: the stage wrappers cd into their own directory before
# invoking "openroad -python", so ORFS's relative RESULTS_DIR would not resolve.
WM_EMBED_ENV = \
	OPENROAD_EXE="$(abspath $(OPENROAD_EXE))" \
	WM_SEED_HEX="$(abspath $(1))" \
	WM_LIB_FILES="$(abspath $(LIB_FILES))" \
	WM_SDC="$(abspath $(2))" \
	WM_SETRC="$(abspath $(PLATFORM_DIR)/setRC.tcl)"

.PHONY: watermark_keygen
watermark_keygen: $(WM_KEY_BUNDLE)

# One "gen_key.sh sign" writes all three stage seeds plus bundle.json.  GNU Make
# 4.2 (this tree) has no grouped targets, so bundle.json is the stamp and each
# seed simply asserts it appeared.
$(WM_SEED_P) $(WM_SEED_C) $(WM_SEED_R): $(WM_KEY_BUNDLE)
	@test -f $@ || { echo "[ERROR WMK-0104] $@ missing after keygen."; exit 1; }

$(WM_KEY_BUNDLE):
	@echo "[INFO WMK-0100] Generating key bundle for $(DESIGN_NAME) (owner=$(WM_OWNER_ID))."
	@cd $(WM_GEN_KEY) && test -f keys/sk.pem || \
		./gen_key.sh keygen --owner-id "$(WM_OWNER_ID)" --out-dir keys
	@cd $(WM_GEN_KEY) && ./gen_key.sh sign --sk keys/sk.pem --pk keys/pk.pem \
		--owner-id "$(WM_OWNER_ID)" --design-id "$(DESIGN_NAME)" \
		--out-dir "out/$(DESIGN_NAME)"

# ==============================================================================
# PLACEMENT
# ==============================================================================
$(RESULTS_DIR)/3_6_place_wm.odb: $(RESULTS_DIR)/3_5_place_dp.odb $(RESULTS_DIR)/3_place.sdc $(WM_SEED_P)
	$(UNSET_AND_MAKE) do-3_6_place_wm

.PHONY: do-3_6_place_wm
do-3_6_place_wm:
	@mkdir -p $(RESULTS_DIR) $(LOG_DIR)
	$(call WM_EMBED_ENV,$(WM_SEED_P),$(RESULTS_DIR)/3_place.sdc) \
	WM_INPUT="$(abspath $(RESULTS_DIR)/3_5_place_dp.odb)" \
	WM_OUTPUT_ODB="$(abspath $(RESULTS_DIR)/3_6_place_wm.odb)" \
	WM_OUTPUT_CELL_LIST="$(abspath $(WM_RESULTS)/wm_place_embed.csv)" \
	$(RUN_CMD) --log $(abspath $(LOG_DIR)/3_6_place_wm.log) --tee -- \
		$(WM_HOME)/placement_wm/place_wm.sh embed

# ==============================================================================
# CTS
# ==============================================================================
$(RESULTS_DIR)/4_2_cts_wm.odb: $(RESULTS_DIR)/4_1_cts.odb $(RESULTS_DIR)/4_cts.sdc $(WM_SEED_C)
	$(UNSET_AND_MAKE) do-4_2_cts_wm

.PHONY: do-4_2_cts_wm
do-4_2_cts_wm:
	@mkdir -p $(RESULTS_DIR) $(LOG_DIR)
	$(call WM_EMBED_ENV,$(WM_SEED_C),$(RESULTS_DIR)/4_cts.sdc) \
	WM_CTS_INPUT="$(abspath $(RESULTS_DIR)/4_1_cts.odb)" \
	WM_CTS_OUTPUT_ODB="$(abspath $(RESULTS_DIR)/4_2_cts_wm.odb)" \
	WM_CTS_OUTPUT_CSV="$(abspath $(WM_RESULTS)/wm_cts_embed.csv)" \
	$(RUN_CMD) --log $(abspath $(LOG_DIR)/4_2_cts_wm.log) --tee -- \
		$(WM_HOME)/cts_wm/cts_wm.sh embed

# ==============================================================================
# VERIFY / CERTIFY
# ==============================================================================
# Ownership is decided by extraction rate against a threshold, not by whether
# every single claim survived: routing and filling legitimately disturb a few
# marked objects.  The stage verifiers exit 2 as soon as one claim fails, so
# their exit status is recorded rather than propagated -- otherwise a design
# with r_P = 0.99 would abort the run and never check CTS at all.
WM_TAU ?= 0.75

.PHONY: watermark_verify
watermark_verify:
	@mkdir -p $(LOG_DIR)
	@echo "[INFO WMK-0101] Verifying placement watermark."
	-@OPENROAD_EXE="$(abspath $(OPENROAD_EXE))" \
	WM_VERIFY_INPUT="$(abspath $(RESULTS_DIR)/6_final.odb)" \
	WM_CELL_LIST="$(abspath $(WM_RESULTS)/wm_place_embed.csv)" \
		$(WM_HOME)/placement_wm/place_wm.sh verify \
		2>&1 | tee $(LOG_DIR)/watermark_verify_placement.log
	@echo "[INFO WMK-0102] Verifying CTS watermark."
	-@OPENROAD_EXE="$(abspath $(OPENROAD_EXE))" \
	WM_CTS_VERIFY_INPUT="$(abspath $(RESULTS_DIR)/6_final.odb)" \
	WM_CELL_LIST="$(abspath $(WM_RESULTS)/wm_cts_embed.csv)" \
		$(WM_HOME)/cts_wm/cts_wm.sh verify \
		2>&1 | tee $(LOG_DIR)/watermark_verify_cts.log
	@$(WM_HOME)/wm_summarize_verify.sh \
		"$(LOG_DIR)/watermark_verify_placement.log" \
		"$(LOG_DIR)/watermark_verify_cts.log" \
		"$(WM_TAU)"

.PHONY: watermark_certify
watermark_certify:
	@DESIGN="$(DESIGN_NAME)" PLATFORM="$(PLATFORM)" \
	WM_FLOW_VARIANT="$(FLOW_VARIANT)" WM_RESULTS="$(WM_RESULTS)" \
	OPENROAD_EXE="$(OPENROAD_EXE)" \
		$(WM_HOME)/certificate/cert.sh certify \
			--results-dir "$(WM_RESULTS)" --stages placement,cts,routing

endif  # WATERMARK

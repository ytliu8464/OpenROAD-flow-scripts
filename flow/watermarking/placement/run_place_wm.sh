
export AES_RES=../../results/nangate45/aes/watermarking-test1
export WM_INPUT="${AES_RES}/3_place.odb"
export WM_OUTPUT_ODB="${AES_RES}/3_place_watermarked_test.odb"
export WM_OUTPUT_DEF="${AES_RES}/3_place_watermarked_test.def"       # optional
export WM_OUTPUT_CELL_LIST="${AES_RES}/wm_cells_embed.csv"           # watermark cell list (embed)
export WM_VERIFY_CELL_LIST="${AES_RES}/wm_cells_verify.csv"          # watermark cell list (verify)
export WM_MESSAGE='Placed-with-watermark-test' WM_KEY='This-is-a-secret-key' WM_NUM_CELLS=100

# ./place_wm.sh embed          # embed only
# ./place_wm.sh verify         # set WM_VERIFY_INPUT first, or use ./place_wm.sh all
# ./place_wm.sh verify_stages  # verify the watermarks across stages
# ./place_wm.sh all            # embed then verify

./place_wm.sh all
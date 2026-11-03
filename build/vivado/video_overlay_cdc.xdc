# Video overlay clock + CDC constraints (top level, FZ3B block design, FSF_VIDEO=1).
# The overlay is the BD module cell "ovl"; its path depends on the top
# (qcnn_zu_i/ovl/inst, or bd/cam_bd_i/ovl/inst with FSF_CAM=1), so it is looked
# up once. SCOPED_TO_REF does not work: the BD renames the module.

# video clock from the PS (VPLL -> dp_video_ref_clk) = the DP mode's pixel clock:
# ~33 MHz for the 800x480 panel; constrained for 74.25 MHz (720p60), dp_live.py
# refuses larger modes.
set ps8    [get_cells -hier -filter {REF_NAME == PS8}]
create_clock -name vclk -period 13.468 [get_pins $ps8/DPVIDEOREFCLK]

set ovl    [get_cells -hier -filter {NAME =~ */ovl/inst && IS_PRIMITIVE == 0}]
set aclk_p [get_property PERIOD [get_clocks clk_pl_0]]
set vclk_p [get_property PERIOD [get_clocks vclk]]

# 1. single-bit toggles into ASYNC_REG 2-FF synchronizers
set_max_delay -datapath_only -from [get_cells $ovl/u_regs/commit_tgl_q_reg]  -to [get_cells "$ovl/u_regs/commit_s_reg[0]"] $vclk_p
set_max_delay -datapath_only -from [get_cells $ovl/u_regs/v_ack_tgl_q_reg]   -to [get_cells "$ovl/u_regs/ack_s_reg[0]"]    $aclk_p
set_max_delay -datapath_only -from [get_cells $ovl/u_regs/v_frame_tgl_q_reg] -to [get_cells "$ovl/u_regs/frame_s_reg[0]"]  $aclk_p

# 2. staged bus st_* -> ap_*: held stable from the commit toggle until the ack
#    returns (>= 2 vclk + 2 aclk), captured only at vblank -> skew bound one vclk
set_max_delay -datapath_only -from [get_cells "$ovl/u_regs/st_*_reg[*]"] -to [get_cells "$ovl/u_regs/ap_*_reg[*]"] $vclk_p

# 3. line requests (background u_bg, camera u_cam): toggle into a 2-FF synchronizer;
#    row/first held from the toggle until the next request (one video line)
# (XDC has no foreach: one block per fetcher)
set_max_delay -datapath_only -from [get_cells $ovl/u_bg/req_tgl_q_reg] -to [get_cells "$ovl/u_bg/req_s_reg[0]"] $aclk_p
set_max_delay -datapath_only -from [get_cells "$ovl/u_bg/req_row_q_reg[*] $ovl/u_bg/req_first_q_reg"] \
                             -to   [get_cells "$ovl/u_bg/row_q_reg[*] $ovl/u_bg/first_q_reg"] $aclk_p
set_max_delay -datapath_only -from [get_cells $ovl/u_cam/req_tgl_q_reg] -to [get_cells "$ovl/u_cam/req_s_reg[0]"] $aclk_p
set_max_delay -datapath_only -from [get_cells "$ovl/u_cam/req_row_q_reg[*] $ovl/u_cam/req_first_q_reg"] \
                             -to   [get_cells "$ovl/u_cam/row_q_reg[*] $ovl/u_cam/first_q_reg"] $aclk_p

# 4. dual-clock RAMs (line buffers, profile, text): written in aclk, read in vclk.
#    BRAM ports are clocked separately; Vivado reports them as safe (no constraint needed).

# 5. video reset synchronizer: aresetn asserts it asynchronously (PRE pins)
set_false_path -to [get_pins "$ovl/vrst_s_reg[*]/PRE"]

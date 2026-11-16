; Compatible full ay driver. State: ay_vars.inc.
; For a smaller build select ay_core.inc and only needed leaves.

    include "ay_init_keep_mixer.inc"
    include "ay_core.inc"
    include "ay_ready.inc"
    include "ay_flush.inc"
    include "ay_read_block.inc"

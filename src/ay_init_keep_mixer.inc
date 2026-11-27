; Optional AY mixer-preserving initialization. Requires ay_core.inc.
; Same register/interrupt contract as uart_init; see ay_core.inc.

uart_init_keep_mixer:
    include "ay_init_ports.inc"
    in a, (c)
    or 0x40                     ; enable port A, preserve the mixer settings
    include "ay_init_output.inc"

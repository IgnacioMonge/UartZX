; Optional AY mixer-preserving initialization. Include immediately BEFORE
; ay_core.inc: JR enters its shared initializer. No extra state.
; Same register/interrupt contract as uart_init; see ay_core.inc.

uart_init_keep_mixer:
    ld bc, UART_AY_REGISTER
    ld a, UART_AY_MIXER
    di                          ; a handler must not split select and data
    out (c), a
    in a, (c)                   ; the mixer as the program left it
    or 0x40                     ; port A as output
    jr uart_init_mixer

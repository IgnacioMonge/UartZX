; Both AY init entries need R14 idle before R7 can enable the output pins.
; Inline this sequence so each entry stays below 200 T-states.
    ld bc, UART_AY_REGISTER
    ld a, UART_AY_PORT_A
    di
    out (c), a
    ld b, 0xBF                  ; UART_AY_DATA
    ld a, UART_LINE_IDLE
    out (c), a
    ld b, 0xFF                  ; UART_AY_REGISTER
    ld a, UART_AY_MIXER
    out (c), a

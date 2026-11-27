; A holds the new R7 value, BC addresses the selected register, IRQs are off.
    ld b, 0xBF
    out (c), a
    ld b, 0xFF
    ld a, UART_AY_PORT_A
    out (c), a                  ; leave R14 selected for direct pin access
    xor a
    ld (uart_second_avail), a
    ld (uart_rx_errors), a
    ei
    ret

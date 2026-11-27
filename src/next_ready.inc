; Optional next uart_ready; include after next_core.inc.
; No extra state. Contract and constants: next_core.inc.

uart_ready:
    ld a, (uart_rx_errors)
    or a
    ret nz
    ld bc, UART_TX
    in a, (c)
    and UART_ERROR_MASK + UART_RX_READY
    ret z
    cp UART_RX_READY + 1        ; clean RX-ready returns CF=1
    ret c
    jp uart_latch_rx_error

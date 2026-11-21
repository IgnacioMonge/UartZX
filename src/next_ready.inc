; Optional next uart_ready; include after next_core.inc.
; No extra state. Contract and constants: next_core.inc.

uart_ready:
    ld a, (uart_rx_errors)
    or a
    ret nz
    ld bc, UART_TX
    in a, (c)
    and UART_STATUS_MASK
    cp UART_RX_OVERFLOW
    jp nc, uart_latch_rx_error  ; returns for this routine, CF=0
    rrca
    ret

; Optional Next TX completion query; include after next_core.inc.
; uart_tx_idle: CF=1 only when both the TX FIFO and the serial transmitter
; are idle (status bit 4). CF=0 while bytes are queued or shifting out.
; One status sample, with RX errors latched even while TX is busy.
; Destroys A, BC; preserves DE, HL and interrupt state. No extra state.
; The caller owns the deadline/RX service while waiting and serializes writers
; before changing baud or resetting; this query does not reserve the UART.
; Requires status bit 4 support; there is no old-core capability probe here.

uart_tx_idle:
    ld bc, UART_TX
    in a, (c)
    call uart_latch_rx_error
    and 0x10
    ret z
    scf
    ret

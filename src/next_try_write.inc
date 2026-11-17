; Optional immediate Next TX attempt; include after next_core.inc.
; uart_try_write: A = byte, CF=0 queued. CF=1 not sent: uart_tx_failed=0
; means temporarily full (retry after servicing RX); nonzero means an earlier
; uart_write failed (recover with uart_init). Busy never latches a TX failure.
; At most one status sample, preserving any RX errors it clears. Does not
; consume RX. Preserves A, BC, DE, HL and interrupt state; no extra state.
; Shares the core's send/failure epilogues and their stack layout.

uart_try_write:
    push bc
    push af
    ld a, (uart_tx_failed)
    or a
    jp nz, uart_write_failed
    ld bc, UART_TX
    in a, (c)
    and UART_STATUS_MASK
    cp UART_RX_OVERFLOW
    call nc, uart_latch_rx_error
    and UART_TX_FULL
    jp nz, uart_write_failed
    jp uart_write_send

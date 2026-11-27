; Optional next uart_flush; include after next_core.inc.
; No extra state. Contract and constants: next_core.inc.

uart_flush:
    push hl
    ld bc, UART_TX
    ld hl, UART_FLUSH_MAX + 1   ; bound samples with a byte or a new RX error
uart_flush_quiet:
    ld de, UART_FLUSH_QUIET
uart_flush_poll:
    in a, (c)                   ; a status read clears hardware error bits
    and UART_ERROR_MASK + UART_RX_READY
    jr nz, uart_flush_active
    dec de
    ld a, d
    or e
    jr nz, uart_flush_poll
uart_flush_done:
    ld (uart_rx_errors), a      ; A = 0 and CF=0 from the count that ran out
    pop hl
    ret
uart_flush_active:
    cp UART_RX_OVERFLOW
    call nc, uart_latch_rx_error
    rrca
    jr nc, uart_flush_count      ; an error without a byte is still activity
    inc b
    in a, (c)                   ; discard
    dec b
uart_flush_count:
    dec hl
    ld a, h
    or l
    jr nz, uart_flush_quiet
    ld hl, uart_rx_errors       ; gave up: reads stay refused until a
    set 2, (hl)                 ; flush completes (UART_RX_OVERFLOW)
    pop hl
    scf
    ret

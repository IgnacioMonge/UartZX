; Optional next uart_flush; include after next_core.inc.
; No extra state. Contract and constants: next_core.inc.

uart_flush:
    push hl
    ld bc, UART_TX
    ld hl, UART_FLUSH_MAX + 1   ; the extra byte tells whether more is coming
uart_flush_quiet:
    ld de, UART_FLUSH_QUIET
uart_flush_poll:
    in a, (c)                   ; clears the hardware error bits as it goes
    rrca
    jr c, uart_flush_byte
    dec de
    ld a, d
    or e
    jr nz, uart_flush_poll
uart_flush_done:
    xor a                       ; CF=0
    ld (uart_rx_errors), a
    pop hl
    ret
uart_flush_byte:
    inc b
    in a, (c)                   ; discard
    dec b
    dec hl
    ld a, h
    or l
    jr nz, uart_flush_quiet
    ld hl, uart_rx_errors       ; gave up: reads stay refused until a
    set 2, (hl)                 ; flush completes (UART_RX_OVERFLOW)
    pop hl
    scf
    ret

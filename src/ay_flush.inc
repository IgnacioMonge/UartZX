; Optional ay uart_flush; include after ay_core.inc.
; No extra state. Contract and constants: ay_core.inc.

uart_flush:
    xor a
    ld (uart_rx_errors), a
    ld bc, UART_FLUSH_MAX + 1   ; the extra read tells whether more is coming
uart_flush_next:
    push bc
    call uart_read_raw
    pop bc
    jr c, uart_flush_byte
    ld a, (uart_rx_errors)
    or a
    ret z                       ; the line went quiet: CF=0
    xor a
    ld (uart_rx_errors), a      ; a damaged byte goes like the others
uart_flush_byte:
    dec bc
    ld a, b
    or c
    jr nz, uart_flush_next
    ld (uart_second_avail), a   ; gave up: keep no byte from before
    scf
    ret

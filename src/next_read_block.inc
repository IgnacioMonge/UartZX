; Optional next uart_read_block; include after next_core.inc.
; No extra state. Contract and constants: next_core.inc.

uart_read_block:
    ld d, b                     ; D = room
    ld a, (uart_rx_errors)
    or a
    ret nz
    ld bc, UART_TX
    in a, (c)
    and UART_STATUS_MASK
    cp UART_RX_OVERFLOW
    jr nc, uart_read_block_error
    rrca
    ret nc                      ; nothing waiting
uart_read_block_byte:
    inc b
    in a, (c)
    dec b
    ld (hl), a
    inc hl
    dec d
    ret z                       ; buffer full, CF=1 from the ready test
    ld e, UART_IDLE_POLLS
uart_read_block_poll:
    in a, (c)
    and UART_STATUS_MASK
    cp UART_RX_OVERFLOW
    jr nc, uart_read_block_error
    rrca
    jr c, uart_read_block_byte
    dec e
    jr nz, uart_read_block_poll
    ret                         ; line idle, CF=0
uart_read_block_error:
    call uart_latch_rx_error
    or a                        ; CF=0
    ret

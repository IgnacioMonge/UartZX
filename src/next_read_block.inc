; Optional next uart_read_block; include after next_core.inc.
; No extra state. Contract and constants: next_core.inc.

uart_read_block:
    ld d, b                     ; D = room
    ld a, (uart_rx_errors)
    or a
    ret nz
    ld bc, UART_TX
    in a, (c)
    and UART_ERROR_MASK + UART_RX_READY
    ret z                       ; nothing waiting, CF=0
    cp UART_RX_READY + 1
    jr nc, uart_read_block_error
uart_read_block_byte:
    inc b                       ; UART_RX
    ini                         ; store, HL+1, B back to UART_TX
    dec d
    scf                         ; INI leaves CF undefined on real hardware
    ret z                       ; buffer full, CF=1
    ld e, UART_IDLE_POLLS
uart_read_block_poll:
    in a, (c)
    and UART_ERROR_MASK + UART_RX_READY
    cp UART_RX_READY
    jr z, uart_read_block_byte  ; common path while the FIFO has data
    jr nc, uart_read_block_error
    dec e
    jr nz, uart_read_block_poll
    xor a                       ; the empty sample compared below RX-ready
    ret
uart_read_block_error:
    jp uart_latch_rx_error      ; returns for this routine; CF=0 from the test

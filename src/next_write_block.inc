; Next UART: faster block send. Optional; include it after next.inc. It
; does not replace uart_write_block from send.inc.
;
;   uart_write_block_fast  HL = data, BC = length. The contract of
;                    uart_write_block: CF=0 all sent, BC = 0; CF=1 a byte was
;                    not sent, HL at that byte and BC = bytes left. It makes
;                    the same checks: every byte follows a not-full status
;                    read that keeps the receive errors, a full FIFO gets the
;                    wait of uart_write, and a failed write stops it. Also
;                    destroys DE. 77 T-states a byte while the TX FIFO has
;                    room, against 183 with send.inc, counted without the
;                    wait states a Next adds at 28 MHz. On those counts it
;                    outruns 2000000 baud at 28 MHz, where 183 does not.
;                    Like uart_write_block
;                    it reads no input while it sends (see send.inc).

uart_write_block_fast:
    ld a, b
    or c
    ret z                       ; nothing to send: CF=0, as uart_write_block
    ld a, (uart_tx_failed)
    add a, 0xFF                 ; CF=1 after a failed write: send nothing
    ret c
    ld d, b
    ld e, c                     ; DE = bytes left
    ld bc, UART_TX
uart_write_block_fast_byte:
    in a, (c)
    and UART_ERROR_MASK + UART_TX_FULL
    jr nz, uart_write_block_fast_status
uart_write_block_fast_send:
    ld a, (hl)
    out (c), a
uart_write_block_fast_sent:
    inc hl
    dec de
    ld a, d
    or e                        ; CF=0
    jr nz, uart_write_block_fast_byte
uart_write_block_fast_done:
    ld b, d
    ld c, e
    ret
uart_write_block_fast_status:
    cp UART_TX_FULL
    call nz, uart_latch_rx_error
    and UART_TX_FULL
    jr z, uart_write_block_fast_send
    ld a, (hl)
    call uart_write             ; waits for room, or fails and latches
    jr nc, uart_write_block_fast_sent
    jr uart_write_block_fast_done

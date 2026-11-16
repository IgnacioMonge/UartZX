; Optional divmmc uart_read_block; include after divmmc_core.inc.
; No extra state. Contract and constants: divmmc_core.inc.

uart_read_block:
    ld d, b                     ; D = room
    ld bc, ZXUNO_ADDR
    ld a, UART_STAT_REG
    out (c), a
    inc b                       ; status stays selected while polling
    in a, (c)
    add a, a
    ret nc                      ; nothing waiting
uart_read_block_byte:
    dec b
    ld a, UART_DATA_REG
    out (c), a
    inc b
    in a, (c)                   ; RTS drops here: the ESP sends the next byte
    ld (hl), a
    inc hl
    dec b
    ld a, UART_STAT_REG
    out (c), a
    inc b
    dec d
    ret z                       ; buffer full, CF=1 from the last ready test
    ld e, UART_IDLE_POLLS
uart_read_block_poll:
    ; Four samples per round keep the wait between a byte arriving and its
    ; read short: with flow control that wait is the cost of each byte.
    in a, (c)
    add a, a
    jr c, uart_read_block_byte
    in a, (c)
    add a, a
    jr c, uart_read_block_byte
    in a, (c)
    add a, a
    jr c, uart_read_block_byte
    in a, (c)
    add a, a
    jr c, uart_read_block_byte
    dec e
    jr nz, uart_read_block_poll
    ret                         ; line idle, CF=0

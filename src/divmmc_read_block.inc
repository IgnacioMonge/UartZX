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
    ini                         ; store, HL+1, B back to ZXUNO_ADDR. RTS drops
                                ; here: the ESP sends the next byte
    inc a                       ; UART_STAT_REG = UART_DATA_REG + 1
    out (c), a
    inc b
    dec d
    scf                         ; INI leaves CF undefined on real hardware
    ret z                       ; buffer full, CF=1
    ld e, UART_IDLE_POLLS
uart_read_block_poll:
    ; Four samples per round keep the wait between a byte arriving and its
    ; read short: with flow control that wait is the cost of each byte.
    in a, (c)
    jp m, uart_read_block_byte
    in a, (c)
    jp m, uart_read_block_byte
    in a, (c)
    jp m, uart_read_block_byte
    in a, (c)
    jp m, uart_read_block_byte
    dec e
    jr nz, uart_read_block_poll
    or a                        ; IN preserves the carry from the last byte
    ret

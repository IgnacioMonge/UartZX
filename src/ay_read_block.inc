; Optional ay uart_read_block; include after ay_core.inc.
; No extra state. Contract and constants: ay_core.inc.

uart_read_block:
    ld d, b                     ; D = room
uart_read_block_next:
    call uart_read
    ret nc
    ld (hl), a
    inc hl
    dec d
    jr nz, uart_read_block_next
    scf
    ret

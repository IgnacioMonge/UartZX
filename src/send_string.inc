; Optional string send; include after any core. No extra state.
; Calls uart_write (A = byte, CF=1 when not sent, preserves BC and HL).

; HL = NUL-terminated string. CF=0 all sent, HL at the terminator. CF=1 a
; byte was not sent, HL at that byte. Destroys A.
uart_write_string:
    ld a, (hl)
    or a
    ret z
    call uart_write
    ret c
    inc hl
    jr uart_write_string

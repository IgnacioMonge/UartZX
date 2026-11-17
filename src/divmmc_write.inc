; Optional compatible A-input adapter around divmmc_fast.inc.
; A = byte. CF=0 sent, CF=1 failed. Preserves A/BC/DE/HL and the fast
; entry's other guarantees. Does not check state or duplicate the TX loop.
uart_write:
    push bc
    push de
    ld e, l                     ; fast entry preserves E and H
    ld l, a
    call uart_write_fast
    ld a, l                     ; fast entry and failure destination preserve L
    ld l, e
    pop de
    pop bc
    ret

; Test consumer, not driver policy: a linear buffer replaces an application ring.
; HL = TX pointer, BC = TX length (nonzero), DE = RX destination with enough room.
; Service RX BEFORE each send; both loops are bounded. No callback or ISR.
; A real application must also route uart_rx_errors to its parser/recovery owner,
; check RX room before consuming, and propagate TX failures to its disconnect path.
duplex_send:
    push bc
    push hl
    ex de, hl
    ld b, 32
    call uart_read_block
    ex de, hl                   ; DE = next free receive position
    pop hl
    pop bc
    ld a, (hl)
    call uart_write
    ret c
    inc hl
    dec bc
    ld a, b
    or c
    jr nz, duplex_send
    ret

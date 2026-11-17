; One receive step for any backend; include after its core. No extra state.
; HL = writable destination with room for one byte. CF=1: stored, HL advanced;
; CF=0: nothing stored, HL unchanged. Destroys A, BC (also alternate set on AY).
; The application inspects uart_rx_errors on Next/AY and owns recovery/deadlines.
; Call unconditionally: AY uart_ready only sees its second-byte cache, so
; testing ready before every read would never start reception on that backend.
; AY may wait ~3.9 ms with interrupts off and returns with them enabled;
; divMMC/Next return immediately and preserve interrupt state.

receive_step:
    call uart_read
    ret nc
    ld (hl), a
    inc hl
    ret

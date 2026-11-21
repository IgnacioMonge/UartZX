; Optional Next recovery with a global status-sample budget; needs next_core.inc.
; uart_flush_budget: DE = samples (1..65535; 0 = 65536). Each sample consumes
; budget, including samples that find bytes. A byte restarts only the quiet
; window, never the global budget. Use at least UART_FLUSH_QUIET samples to
; allow success. CF=0 after that many consecutive empty samples, clearing RX
; errors; CF=1 on exhaustion, latching UART_RX_OVERFLOW as uart_flush does.
; A later completed flush or uart_init reopens guarded RX. No extra state.
; Destroys A, BC, DE; preserves HL and interrupt state. This bounds work,
; not wall time: interrupt handlers and physical wait states add latency.

uart_flush_budget:
    push hl
    ld bc, UART_TX
    ld hl, UART_FLUSH_QUIET
uart_flush_budget_poll:
    in a, (c)
    and UART_STATUS_MASK
    cp UART_RX_OVERFLOW
    call nc, uart_latch_rx_error
    rrca
    jr c, uart_flush_budget_byte
    dec hl
    ld a, h
    or l
    jr nz, uart_flush_budget_count
uart_flush_budget_done:
    ld (uart_rx_errors), a      ; A = 0 and CF=0 from the window that ran out
    pop hl
    ret
uart_flush_budget_byte:
    inc b
    in a, (c)
    dec b
    ld hl, UART_FLUSH_QUIET
uart_flush_budget_count:
    dec de
    ld a, d
    or e
    jr nz, uart_flush_budget_poll
    ld hl, uart_rx_errors
    set 2, (hl)                 ; recovery incomplete, as in uart_flush
    pop hl
    scf
    ret

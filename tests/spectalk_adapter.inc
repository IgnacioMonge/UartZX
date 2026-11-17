; Compatible adoption specimen (Next or existing A-input divMMC consumers).
; Minimal Classic uses examples/classic_uart.inc instead, without this gate.
; uart_tx_failed aliases the application's _uart_tx_failed; do not allocate twice.
; _uart_tx_fail owns normalization to 1 AND the immediate disconnect effects.
_ay_uart_send:
    ld a, (_uart_tx_failed)
    or a
    scf
    ret nz                      ; preserve pending=1 and already-reported=2
    ld a, l
    call uart_write
    ret nc
    jp _uart_tx_fail             ; normalize state and run immediate effects once

; Same 10 frame calls + 512 bounded reads as the reference consumer.
; Application _frame_wait owns IY, interrupt and overlay requirements.
_ay_uart_init:
    call uart_init
    ld b, 10
spectalk_init_wait:
    push bc
    call uart_read
    pop bc
    call _frame_wait
    djnz spectalk_init_wait
    ld de, 512
spectalk_init_flush:
    call uart_read
    dec de
    ld a, d
    or e
    jr nz, spectalk_init_flush
    ret

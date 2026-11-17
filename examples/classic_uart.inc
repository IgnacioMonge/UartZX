; SpecTalkZX Classic adoption specimen. Shared syntax, no extra state.
; z88dk consumer: SECTION code_user; EXTERN _frame_wait, _uart_tx_failed,
; _uart_tx_fail; PUBLIC _ay_uart_init, _ay_uart_send, uartRead.
; _uart_tx_fail must set state 1, disconnect/reset SNTP and return CF=1,
; preserving E/HL/IX/IY/IFF. States 1/2 bypass it without touching ports.
uart_tx_failed EQU _uart_tx_failed
uart_tx_fail EQU _uart_tx_fail
_ay_uart_send EQU uart_write_fast
uartRead EQU uart_read
    include "../src/divmmc_fast.inc"

; Same ten frame waits and 512 bounded reads as the consumer.
; _frame_wait owns IY, interrupt and overlay requirements.
_ay_uart_init:
    call uart_init
    ld b, 10
classic_init_wait:
    push bc
    call uartRead
    pop bc
    call _frame_wait
    djnz classic_init_wait
    ld de, 512
classic_init_flush:
    call uartRead
    dec de
    ld a, d
    or e
    jr nz, classic_init_flush
    ret

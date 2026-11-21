; Optional Next reset control, without HALT or boot waits. Requires next_core.inc.
; uart_esp_reset_assert holds ESP AND expansion bus in reset; release writes zero.
; Caller owns pulse duration, boot wait and UART reinitialization afterwards.
; Preserves E, HL, IX/IY and the interrupt enable state; destroys AF, BC, D.
; Serializes the NextReg select/data pair against maskable interrupts. As with
; other NextReg users, an NMI handler must not change selection during the pair.

; uart_esp_reset_release stays the first instruction of this file:
; next_esp_reset.inc falls into it.
uart_esp_reset_release:
    ld d, 0
    defb 0x01                   ; LD BC,nn: its operand hides the next LD
uart_esp_reset_assert:
    ld d, 0x80
uart_esp_reset_write:
    ld a, i                     ; P/V = IFF2
    push af
    di
    ld bc, NEXTREG_SELECT
    ld a, NEXTREG_RESET
    out (c), a
    inc b
    out (c), d
    pop af
    ret po
    ei
    ret

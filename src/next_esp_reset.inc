; Compatible frame-wait reset. Includes next_reset.inc; do not include it twice.
; Needs frame interrupts enabled; returns enabled. Destroys AF, BC, D.
; No boot wait: the application owns that, and calls uart_init afterwards.

uart_esp_reset:
    call uart_esp_reset_assert
    ld b, UART_RESET_FRAMES
uart_esp_reset_hold:
    halt
    djnz uart_esp_reset_hold
    jp uart_esp_reset_release

    include "next_reset.inc"

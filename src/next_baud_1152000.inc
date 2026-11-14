; Next UART divisors for 1152000 baud, indexed by NextReg 0x11 (video timing).
;     ld hl, uart_baud_1152000
;     call uart_set_baud
uart_baud_1152000:
    defw 24, 25, 26, 26, 27, 28, 29, 23

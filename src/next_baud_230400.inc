; Next UART divisors for 230400 baud, indexed by NextReg 0x11 (video timing).
;     ld hl, uart_baud_230400
;     call uart_set_baud
uart_baud_230400:
    defw 122, 124, 128, 130, 135, 139, 143, 117

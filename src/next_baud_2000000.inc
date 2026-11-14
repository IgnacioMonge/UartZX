; Next UART divisors for 2000000 baud, indexed by NextReg 0x11 (video timing).
;     ld hl, uart_baud_2000000
;     call uart_set_baud
uart_baud_2000000:
    defw 14, 14, 15, 15, 16, 16, 17, 14

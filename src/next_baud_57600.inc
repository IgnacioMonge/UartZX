; Next UART divisors for 57600 baud, indexed by NextReg 0x11 (video timing).
;     ld hl, uart_baud_57600
;     call uart_set_baud
uart_baud_57600:
    defw 486, 496, 512, 521, 538, 556, 573, 469

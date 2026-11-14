; Next UART divisors for 9600 baud, indexed by NextReg 0x11 (video timing).
;     ld hl, uart_baud_9600
;     call uart_set_baud
uart_baud_9600:
    defw 2917, 2976, 3069, 3125, 3229, 3333, 3438, 2813

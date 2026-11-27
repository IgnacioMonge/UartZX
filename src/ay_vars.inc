; AY bit-banged UART driver: state. Include it once, in RAM.
; Zero-initialised: a z88dk consumer may place it in a bss section.

; uart_second_avail is 1 while uart_second_byte holds a byte, 0 otherwise.
; uart_second_byte must directly follow it: the driver steps from one to
; the other with INC HL.
uart_second_avail:
    defb 0
uart_second_byte:
    defb 0
; Bit mask: UART_RX_FRAMING after a bad stop bit, UART_RX_RECOVERY after an
; incomplete flush. A raw read can set both. Cleared by a completed flush or init.
uart_rx_errors:
    defb 0

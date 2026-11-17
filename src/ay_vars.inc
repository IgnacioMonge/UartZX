; AY bit-banged UART driver: state. Include it once, in RAM.
; Zero-initialised: a z88dk consumer may place it in a bss section.

; uart_second_byte must directly follow uart_second_avail: the driver steps
; from one to the other with INC HL.
uart_second_avail:
    defb 0
uart_second_byte:
    defb 0
; UART_RX_FRAMING after a stop bit read low; UART_RX_RECOVERY after an
; incomplete flush. Cleared by a completed uart_flush or uart_init.
uart_rx_errors:
    defb 0

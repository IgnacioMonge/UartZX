; AY bit-banged UART driver: state. Include it once, in RAM.
; Zero-initialised: a z88dk consumer may place it in a bss section.

; uart_second_byte must directly follow uart_second_avail: the driver steps
; from one to the other with INC HL.
uart_second_avail:
    defb 0
uart_second_byte:
    defb 0

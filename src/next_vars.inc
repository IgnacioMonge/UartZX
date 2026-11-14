; ZX Spectrum Next UART driver: state. Include it once, in RAM.
; Zero-initialised: a z88dk consumer may place it in a bss section.

; Not zero after a failed uart_write. Cleared by uart_init.
uart_tx_failed:
    defb 0
; Error bits seen in status reads (UART_RX_OVERFLOW, UART_RX_FRAMING).
; Cleared by uart_flush and uart_init.
uart_rx_errors:
    defb 0

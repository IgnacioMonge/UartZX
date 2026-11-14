; divMMC / ZX-Uno UART driver: state. Include it once, in RAM.
; Zero-initialised: a z88dk consumer may place it in a bss section.

; Not zero after a failed uart_write. Cleared by uart_init.
uart_tx_failed:
    defb 0

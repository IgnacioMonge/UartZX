; divMMC / ZX-Uno UART driver: state. Include it once, in RAM.
; Zero-initialised: a z88dk consumer may place it in a bss section.

; 0=operational, default failure=1. Application state 2 remains fail-stopped.
; Cleared by uart_init. Omit this file when aliasing application-owned storage.
uart_tx_failed:
    defb 0

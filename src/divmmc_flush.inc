; Optional divmmc uart_flush; include after divmmc_core.inc.
; No extra state. Contract and constants: divmmc_core.inc.

uart_flush:
    ld de, UART_FLUSH_POLLS
uart_flush_loop:
    call uart_read
    dec de
    ld a, d
    or e
    jr nz, uart_flush_loop
    ret

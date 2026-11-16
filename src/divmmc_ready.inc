; Optional divmmc uart_ready; include after divmmc_core.inc.
; No extra state. Contract and constants: divmmc_core.inc.

uart_ready:
    ld bc, ZXUNO_ADDR
    ld a, UART_STAT_REG
    out (c), a
    inc b
    in a, (c)
    add a, a                    ; RX-ready -> CF
    ret

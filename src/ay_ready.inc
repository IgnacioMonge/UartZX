; Optional ay uart_ready; include after ay_core.inc.
; No extra state. Contract and constants: ay_core.inc.

uart_ready:
    ld a, (uart_rx_errors)
    or a
    ret nz                      ; CF=0 while errors are latched
    ld a, (uart_second_avail)
    rra                         ; 1 = a byte is kept: CF=1
    ret

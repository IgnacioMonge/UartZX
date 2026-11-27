; Optional ay uart_ready; include after ay_core.inc.
; No extra state. Contract and constants: ay_core.inc.

uart_ready:
    ld a, (uart_second_avail)
    rra
    ret nc                      ; the cache is usually empty
    ld a, (uart_rx_errors)
    cp 1                        ; CF=1 only for a clean cached byte
    ret

; Default first-time TX failure destination for divmmc_fast.inc.
; A consumer with immediate failure effects supplies uart_tx_fail instead.
; 0=operational, 1=pending report, 2=already reported. Only init clears it.
uart_tx_fail:
    ld a, 1
    ld (uart_tx_failed), a
    scf
    ret

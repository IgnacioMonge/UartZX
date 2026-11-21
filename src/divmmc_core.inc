; Compatible minimal duplex driver: same init/read/raw read/write API.
; uart_write takes A and preserves A/BC/DE/HL. One shared TX algorithm.
; For L-input TX without saves, select divmmc_fast.inc instead and supply
; uart_tx_fail (or include divmmc_tx_fail.inc for the default latch).

    include "divmmc_fast_open.inc"
    include "divmmc_tx_fail.inc"
    include "divmmc_write.inc"

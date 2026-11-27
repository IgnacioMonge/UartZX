; Optional Next duplex selection: 371 code/read-only bytes, plus next_vars.inc.
; Include once; allocate the two state bytes separately in writable RAM.
; Caller checks board support, configures ESP UART_CUR and bounds transactions.
; uart_init starts at 115200 with flow off; enable after ESP negotiation.
    include "../src/next_core.inc"
    include "../src/next_try_write.inc"
    include "../src/next_flush_budget.inc"
    include "../src/next_tx_idle.inc"
    include "../src/next_flow.inc"
    include "../src/next_baud_khz.inc"
    include "../src/next_baud_2000000.inc"

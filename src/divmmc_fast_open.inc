; divMMC / ZX-Uno fast duplex core, left open at its end: after 256 busy TX
; samples it runs into whatever is assembled right after this file. That must
; be the failure destination: include divmmc_tx_fail.inc next (3 bytes less
; than the jump), or use divmmc_fast.inc, which ends in `jp uart_tx_fail` for
; a destination elsewhere. Contract, hardware model and entry points:
; divmmc_fast.inc. Requires uart_tx_failed storage.
; This is the code of divmmc_fast.inc without that jump, in a file of its own
; so that neither needs an include path; a test keeps them byte for byte
; alike. Change both together.

; ZX-Uno compatible register interface. With C = 0x3B the two ports differ
; only in B: INC B / DEC B switch between them.
UART_DATA_REG       EQU 0xC6
UART_STAT_REG       EQU 0xC7
UART_BYTE_RECEIVED  EQU 0x80
UART_BYTE_SENDING   EQU 0x40
ZXUNO_ADDR          EQU 0xFC3B
ZXUNO_REG           EQU 0xFD3B

; Reads made by uart_flush (15 to 21 ms at 3.5 MHz).
UART_FLUSH_POLLS    EQU 512
; uart_read_block gives up after this many rounds of four status samples
; without a byte: about three byte times at 3.5 MHz. The count is in samples,
; so a faster CPU clock ends blocks sooner (still correct, less efficient).
UART_IDLE_POLLS     EQU 8

uart_init:
    xor a
    ld (uart_tx_failed), a
; Falls into uart_read: a stale byte is read, which releases RTS.

; This UART reports no receive errors: the raw read is the read.
uart_read_raw:
uart_read:
    ld bc, ZXUNO_ADDR
    ld a, UART_STAT_REG
    out (c), a
    inc b
    in a, (c)
    add a, a                    ; RX-ready -> CF
    ret nc
    dec b
    ld a, UART_DATA_REG
    out (c), a
    inc b
    in a, (c)                   ; IN, OUT, INC and DEC keep CF
    ret

uart_write_send:
    dec b
    ld a, UART_DATA_REG
    out (c), a
    inc b
    out (c), l
    or a                        ; CF=0
    ret

uart_write_fast:
    ld a, (uart_tx_failed)
    or a
    scf
    ret nz                      ; preserve pending=1 and already-reported=2
    ld d, a                     ; 0: 256 samples, over four byte times at 28 MHz
    ld bc, ZXUNO_ADDR
    ld a, UART_STAT_REG
    out (c), a
    inc b
uart_write_wait:
    in a, (c)
    and UART_BYTE_SENDING
    jr z, uart_write_send
    dec d
    jr nz, uart_write_wait
; First timeout only. No RET: execution continues in whatever is assembled
; next, which must be the failure destination (see the top of this file).

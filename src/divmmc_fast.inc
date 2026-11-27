; Fast duplex core: init, read/raw read and L-input write.
; divmmc_fast_open.inc is this same code without the final jump; a test
; keeps the two files byte for byte alike. Change both together.
; Requires uart_tx_failed storage and a uart_tx_fail destination (see below).
; divMMC / ZX-Uno UART driver (ZX-Uno compatible UART: divTIESUS, ZX-Uno).
; Compatible storage: divmmc_vars.inc. Hardware fixes the baud rate
; (115200 in the ZX-Uno core); the driver sets none.
;
; Written in the syntax sjasmplus and z88dk-z80asm share: global labels only,
; EQU, 0x literals, no macros, modules or sections. The register names and
; access sequence come, through the application drivers, from netman-zx by
; Alex Nihirash (github.com/nihirash/netman-zx).
;
; Hardware model (ZX-Uno core common/zxunouart.v and uart.v, shared with the
; open divTIESUS CPLD):
;   - A status read changes nothing. Polling never consumes data.
;   - The receiver holds one byte, raises RTS and stops listening until the
;     data register is read. The ESP must honour CTS (SpecTalkZX and
;     NetChessZX send AT+UART_CUR=115200,8,1,0,2), or back-to-back bytes are
;     lost. With CTS,
;     nothing is lost however slowly the driver reads.
;   - A data write while TX is busy is dropped: every write here follows a
;     fresh not-busy sample.
;   - TX has no flow control input: busy lasts one byte. A port reading 0xFF
;     continuously makes TX time out and RX report an endless stream of 0xFF.
;     An unattached port on a 48K can also return screen data during display
;     fetches, so even a successful write is possible with no UART present.
;     Bound each receive loop. Confirm the expected protocol reply in the
;     application when detecting the ESP.
;
; No routine uses DI, EI, HALT, IX, IY or the alternate registers.
;
;   uart_init        Clear the TX failure latch and discard a stale byte.
;                    Destroys A, BC.
;   uart_read        CF=1 and A = byte, or CF=0 when none is waiting.
;                    Destroys BC. Preserves DE, HL. 60 T-states when empty,
;                    103 with a byte.
;   uart_ready       CF=1 when a byte is waiting; it stays waiting.
;                    Destroys A, BC.
;   uart_write_fast  L = byte. CF=0 sent, CF=1 not sent. Destroys AF, BC, D.
;                    Preserves E, HL, IX, IY, alternate registers and IFF.
;                    No stack use beyond the caller's return address.
;                    TX ready on the first sample: 143 T-states, entry to RET.
;                    Checks uart_tx_failed once: any nonzero value rejects
;                    without I/O or invoking uart_tx_fail again.
;                    After 256 busy samples, tail-jumps to uart_tx_fail.
;   uart_tx_fail     Assembly-time destination, not a callback. Must latch a
;                    nonzero failure, return CF=1, preserve E/HL/IX/IY/alternate
;                    registers/IFF and balance SP. AF/BC/D may be destroyed.
;                    For 0/1/2 application state, set 1 and run effects here.
;                    Include divmmc_tx_fail.inc for the default implementation.
;                    Only deliberate uart_init clears fail-stop.
;   uart_write       Optional A-input preserving adapter: divmmc_write.inc.
;                    Preserves A, BC, DE, HL, returns the fast entry's CF.
;   uart_flush       Discard whatever arrives during UART_FLUSH_POLLS reads.
;                    It does not stop at the first gap, and always returns
;                    CF=0: it cannot tell whether more is coming.
;                    Destroys A, BC, DE.
;   uart_read_block  HL = buffer, B = room (1..255, 0 = 256). Stores bytes
;                    until the buffer is full or the line goes idle; returns
;                    at once when nothing is waiting. Out: HL = next free
;                    position, D = room left, CF=1 buffer full, CF=0 line
;                    idle. Destroys A, BC, E.

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
    ret                         ; the not-busy AND cleared CF

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
    jp uart_tx_fail             ; first timeout only; consumer owns its effects

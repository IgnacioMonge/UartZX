; divMMC / ZX-Uno UART driver (ZX-Uno compatible UART: divTIESUS, ZX-Uno).
; Needs divmmc_vars.inc. The driver sets no baud rate: the hardware fixes it
; (115200 in the ZX-Uno core).
;
; Written in the syntax sjasmplus and z88dk-z80asm share: global labels only,
; EQU, 0x literals, no macros, modules or sections. The register names and
; access sequence come, through the application drivers, from netman-zx by
; Alex Nihirash (github.com/nihirash/netman-zx).
;
; Hardware model (ZX-Uno core common/zxunouart.v and uart.v, shared with the
; open divTIESUS CPLD; not proven for every CPLD image in the field):
;   - A status read changes nothing. Polling never consumes data.
;   - The receiver holds one byte, raises RTS and stops listening until the
;     data register is read. The ESP must honour CTS (SpecTalkZX and
;     NetChessZX send AT+UART_CUR=115200,8,1,0,2), or back-to-back bytes are
;     lost. With CTS,
;     nothing is lost however slowly the driver reads.
;   - A data write while TX is busy is dropped: every write here follows a
;     fresh not-busy sample.
;   - TX has no flow control input: busy lasts one byte. A TX timeout means
;     there is no UART (a floating bus reads 0xFF: busy for ever).
;   - The receive side cannot tell a missing UART: 0xFF also has the RX-ready
;     bit set, so uart_ready and uart_read report bytes of 0xFF for ever and
;     uart_read_block fills its buffer. Send before trusting what is read (a
;     failed uart_write is the "no UART" verdict) and bound every drain loop.
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
;   uart_write       A = byte. CF=0 sent. CF=1 not sent: TX stayed busy for
;                    256 samples, or an earlier write failed. The failure
;                    latches (uart_tx_failed) so a command is never sent with
;                    holes; only uart_init clears it. Preserves A, BC, DE, HL.
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

uart_ready:
    ld bc, ZXUNO_ADDR
    ld a, UART_STAT_REG
    out (c), a
    inc b
    in a, (c)
    add a, a                    ; RX-ready -> CF
    ret

uart_write:
    push bc
    push de
    ld e, a                     ; E = byte to send
    ld a, (uart_tx_failed)
    or a
    jr nz, uart_write_failed    ; fail-stop until uart_init
    ld bc, ZXUNO_ADDR
    ld a, UART_STAT_REG
    out (c), a
    inc b
    ld d, 0                     ; 256 samples: over four byte times at 28 MHz
uart_write_wait:
    in a, (c)
    and UART_BYTE_SENDING
    jr z, uart_write_send
    dec d
    jr nz, uart_write_wait
    inc a                       ; A = 0x41: any non-zero value latches
    ld (uart_tx_failed), a
uart_write_failed:
    ld a, e
    pop de
    pop bc
    scf
    ret
uart_write_send:
    dec b
    ld a, UART_DATA_REG
    out (c), a
    inc b
    out (c), e
    ld a, e
    pop de
    pop bc
    or a                        ; CF=0
    ret

uart_flush:
    ld de, UART_FLUSH_POLLS
uart_flush_loop:
    call uart_read
    dec de
    ld a, d
    or e
    jr nz, uart_flush_loop
    ret

uart_read_block:
    ld d, b                     ; D = room
    ld bc, ZXUNO_ADDR
    ld a, UART_STAT_REG
    out (c), a
    inc b                       ; status stays selected while polling
    in a, (c)
    add a, a
    ret nc                      ; nothing waiting
uart_read_block_byte:
    dec b
    ld a, UART_DATA_REG
    out (c), a
    inc b
    in a, (c)                   ; RTS drops here: the ESP sends the next byte
    ld (hl), a
    inc hl
    dec b
    ld a, UART_STAT_REG
    out (c), a
    inc b
    dec d
    ret z                       ; buffer full, CF=1 from the last ready test
    ld e, UART_IDLE_POLLS
uart_read_block_poll:
    ; Four samples per round keep the wait between a byte arriving and its
    ; read short: with flow control that wait is the cost of each byte.
    in a, (c)
    add a, a
    jr c, uart_read_block_byte
    in a, (c)
    add a, a
    jr c, uart_read_block_byte
    in a, (c)
    add a, a
    jr c, uart_read_block_byte
    in a, (c)
    add a, a
    jr c, uart_read_block_byte
    dec e
    jr nz, uart_read_block_poll
    ret                         ; line idle, CF=0

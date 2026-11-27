; Minimal duplex core: init, read/raw read, write and mandatory safety.
; Optional entry points described below live in separate includes.
; ZX Spectrum Next internal UART driver for the ESP. Needs next_vars.inc.
;
; Written in the syntax sjasmplus and z88dk-z80asm share: global labels only,
; EQU, 0x literals, no macros, modules or sections. Plain Z80 opcodes only.
; The port names and the divisor write come, through the application drivers,
; from netman-zx by Alex Nihirash (github.com/nihirash/netman-zx).
;
; Hardware model (ZX Spectrum Next core, src/serial/uart.vhd and ports.txt):
;   - RX has a 512-byte FIFO and TX a 64-byte one. A write to a full TX FIFO
;     is dropped: every write here follows a fresh not-full sample.
;     With flow off, the caller must service RX within the FIFO time budget.
;   - Status bits 2 (RX overflow: a byte was lost) and 6 (framing or parity
;     error) are sticky and cleared by ANY status read. So every status read
;     in this file passes through the error test; code that reads the status
;     port on its own must do the same (see uart_latch_rx_error).
;   - Writing bit 7 of the frame register empties both FIFOs.
;   - The baud divisor is the system clock, which depends on the video timing
;     (NextReg 0x11), divided by the baud rate.
;
; Receive errors: an error bit seen in any status read is kept in
; uart_rx_errors, and the read that sees it reports no byte. From then on
; uart_read, uart_ready and uart_read_block report nothing until uart_flush
; (or uart_init) discards the input and clears the errors: a stream with a
; hole is never handed over as if it were whole. uart_read_raw skips that
; gate for callers with their own policy.
;
; The core preserves interrupt state. Optional reset controls serialize the
; NextReg pair and preserve IFF; the frame-wait reset requires IFF enabled.
; Nothing uses IX, IY or the alternate registers.
;
;   uart_init        Select the ESP UART, empty the FIFOs, 8N1 without flow
;                    control, 115200 baud, clear both failure latches.
;                    Destroys A, BC, DE, HL.
;   uart_set_baud    HL = divisor table (uart_baud_115200 here, the others in
;                    next_baud_*.inc). It only writes the divisor: change the
;                    ESP side first and call uart_flush afterwards. Safe with
;                    interrupts enabled even if a handler uses NextRegs.
;                    Destroys A, BC, DE, HL.
;   uart_read        CF=1 and A = byte, or CF=0: nothing waiting, or errors
;                    latched (uart_rx_errors is not zero). Destroys BC.
;                    Preserves DE, HL. 62 T-states empty, 96 with a byte.
;   uart_read_raw    As uart_read without the latch gate: 40 and 74 T-states.
;                    The read that latches a new error still reports no byte;
;                    the next one carries on.
;   uart_ready       CF=1 when a byte is waiting and no error is latched; the
;                    byte stays waiting. Destroys A, BC.
;   uart_write       A = byte. CF=0 queued. CF=1 not sent: the TX FIFO stayed
;                    full for UART_TX_POLLS samples, or an earlier write
;                    failed. The failure latches (uart_tx_failed) until
;                    uart_init. Preserves A, BC, DE, HL.
;   uart_flush       Discard input until UART_FLUSH_QUIET consecutive samples
;                    show no byte and no new RX error, then clear the error
;                    latch and return CF=0. BREAK alone is a line level.
;                    CF=1 on UART_FLUSH_MAX + 1 samples with bytes or errors:
;                    UART_RX_OVERFLOW is latched, so reads
;                    stay refused until a later flush completes.
;                    Destroys A, BC, DE.
;   uart_read_block  HL = buffer, B = room (1..255, 0 = 256). Stores bytes
;                    until the buffer is full, the line goes idle or an error
;                    shows; returns at once when nothing is waiting or errors
;                    are latched. Out: HL = next free position, D = room
;                    left, CF=1 buffer full, CF=0 otherwise. Destroys A, BC,
;                    E. About 78 T-states per byte while the FIFO has data.
;   uart_esp_reset   Hold the ESP in reset for UART_RESET_FRAMES frames. The
;                    same NextReg bit resets the expansion bus: a device on it
;                    is reset too. Needs interrupts enabled on entry and
;                    returns with them enabled. It counts HALTs: with another
;                    interrupt source enabled besides the frame one the hold
;                    is shorter. The caller waits for the ESP to boot, then
;                    calls uart_flush, or uart_init if a write had failed
;                    (only it clears uart_tx_failed and empties the TX FIFO).
;                    Destroys A, BC, D.
;   uart_latch_rx_error  A = status byte. Adds its error bits to
;                    uart_rx_errors. Preserves everything, flags included.
;
; A consumer that inlines its own receive loop reads status like this:
;     in a, (c)                 ; BC = UART_TX
;     and UART_STATUS_MASK
;     cp UART_RX_OVERFLOW       ; CF=0 when an error bit is set
;     call nc, uart_latch_rx_error

UART_TX             EQU 0x133B  ; read: status, write: byte to send
UART_RX             EQU 0x143B  ; read: received byte, write: baud divisor
UART_SELECT         EQU 0x153B
UART_FRAME          EQU 0x163B
UART_RX_READY       EQU 0x01
UART_TX_FULL        EQU 0x02
UART_RX_OVERFLOW    EQU 0x04
UART_RX_FRAMING     EQU 0x40
UART_ERROR_MASK     EQU 0x44    ; the two bits a status read destroys
UART_STATUS_MASK    EQU 0x47    ; errors, TX full, RX ready
; Bit 6 clear selects the ESP; bit 4 latches divisor bits 16..14 as zero. The
; core ignores bit 5, which every application has always set.
UART_SELECT_ESP     EQU 0x30
UART_FRAME_RESET    EQU 0x98    ; empty the FIFOs, 8N1
UART_FRAME_8N1      EQU 0x18    ; 8N1, no hardware flow control
NEXTREG_SELECT      EQU 0x243B
NEXTREG_DATA        EQU 0x253B
NEXTREG_RESET       EQU 0x02    ; bit 7 holds the ESP and the expansion bus in reset
NEXTREG_VIDEO_TIMING EQU 0x11

; Samples of a full TX FIFO before uart_write gives up: over ten byte times
; at 9600 baud and 28 MHz.
UART_TX_POLLS       EQU 4096
; uart_flush ends after this many clean empty samples in a row: 3.8 ms at
; 28 MHz, 30 ms at 3.5 MHz, without physical waits or interrupt handlers.
UART_FLUSH_QUIET    EQU 2048
UART_FLUSH_MAX      EQU 4096    ; active samples allowed before one more gives up
; uart_read_block gives up after this many samples without a byte: over two
; byte times at 115200 baud and 28 MHz.
UART_IDLE_POLLS     EQU 96
UART_RESET_FRAMES   EQU 25      ; about half a second

uart_init:
    xor a
    ld (uart_tx_failed), a
    ld (uart_rx_errors), a
    ld bc, UART_SELECT
    ld a, UART_SELECT_ESP
    out (c), a
    inc b                       ; UART_FRAME
    ld de, UART_FRAME_RESET * 256 + UART_FRAME_8N1
    out (c), d
    out (c), e
    ld hl, uart_baud_115200

uart_set_baud:
    ld bc, NEXTREG_SELECT
    ld d, 0                     ; 256 tries, so a machine without NextRegs returns
uart_set_baud_timing:
    ld a, NEXTREG_VIDEO_TIMING
    out (c), a
    inc b                       ; NEXTREG_DATA
    in e, (c)
    dec b
    in a, (c)                   ; a handler may have selected another register
    cp NEXTREG_VIDEO_TIMING
    jr z, uart_set_baud_index
    dec d
    jr nz, uart_set_baud_timing
uart_set_baud_index:
    ld a, e
    and 7                       ; timings 0..6 VGA, 7 HDMI
    add a, a
    ld e, a
    ld d, 0
    add hl, de
    ld a, (hl)
    inc hl
    ld h, (hl)
    ld l, a                     ; HL = divisor
    ld b, UART_RX / 256         ; C is already the low byte of every port here
    and 0x7F
    out (c), a                  ; bits 6..0
    add hl, hl
    set 7, h
    out (c), h                  ; bits 13..7
    ret

; Divisors for 115200 baud, indexed by NextReg 0x11 (video timing).
uart_baud_115200:
    defw 243, 248, 256, 260, 269, 278, 286, 234

uart_latch_rx_error:
    push af
    push hl
    and UART_ERROR_MASK
    ld hl, uart_rx_errors
    or (hl)
    ld (hl), a
    pop hl
    pop af
    ret

uart_read:
    ld a, (uart_rx_errors)
    or a
    ret nz                      ; CF=0: no byte from a damaged stream
uart_read_raw:
    ld bc, UART_TX
    in a, (c)
    and UART_ERROR_MASK + UART_RX_READY
    ret z                       ; empty, CF=0
    cp UART_RX_READY + 1        ; only clean RX-ready sets carry
    jr nc, uart_latch_rx_error  ; return for this read with CF=0
    inc b                       ; UART_RX
    in a, (c)                   ; IN and INC keep CF
    ret

uart_write:
    push bc
    push af                     ; the byte
    ld a, (uart_tx_failed)
    or a
    jr nz, uart_write_failed    ; fail-stop until uart_init
    ld bc, UART_TX
    in a, (c)
    and UART_ERROR_MASK + UART_TX_FULL
    jr nz, uart_write_status
uart_write_send:
    pop af
    out (c), a
    pop bc
    or a                        ; CF=0
    ret
uart_write_status:
    cp UART_TX_FULL
    jr z, uart_write_full
    call uart_latch_rx_error
    and UART_TX_FULL
    jr z, uart_write_send
uart_write_full:
    push de
    ld de, UART_TX_POLLS
uart_write_wait:
    in a, (c)
    and UART_STATUS_MASK
    cp UART_RX_OVERFLOW
    call nc, uart_latch_rx_error
    and UART_TX_FULL
    jr z, uart_write_room
    dec de
    ld a, d
    or e
    jr nz, uart_write_wait
    pop de
    inc a
    ld (uart_tx_failed), a
uart_write_failed:
    pop af
    pop bc
    scf
    ret
uart_write_room:
    pop de
    jr uart_write_send

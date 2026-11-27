; Minimal duplex core: init, read/raw read, write and mandatory safety.
; Optional entry points described below live in separate includes.
; AY bit-banged UART driver for the ESP: serial on I/O port A of the
; AY-3-8912. Needs ay_vars.inc; ay_speed.inc is optional.
;
; The receive timing and its polling loops descend, through NetManZX, from
; netman-zx by Alex Nihirash (github.com/nihirash/netman-zx), which
; publishes no licence: see the licence section of the README. The bit loops
; are shorter code written here that keeps every port access on the T-state
; where those loops had it.
;
; Written in the syntax sjasmplus and z88dk-z80asm share: global labels only,
; EQU, 0x literals, no macros, modules or sections.
;
; Wiring (register 14): bit 3 = TX to the ESP, bit 2 = CTS to the ESP (low
; lets it send), bit 7 = RX from the ESP.
;
; What a bit-banged port imposes:
;   - Bits are timed in T-states. Assemble this file in uncontended memory
;     and keep the stack there too: 0x8000..0xBFFF is always safe. On a 128K
;     or +2, 0xC000..0xFFFF is contended while RAM page 1, 3, 5 or 7 is paged
;     there (4 to 7 on a +2A or +3), and with one of those paged in a 128K
;     or +2 also delays every access to the register port 0xFFFD: keep an
;     uncontended page there while the driver runs.
;   - Sending and receiving run with interrupts off, and every routine that
;     touches the port returns with them ENABLED. A frame interrupt that
;     falls inside a routine is lost, not delayed.
;   - Nothing is received outside uart_read: CTS is low only while it
;     listens. The ESP must honour CTS (AT+UART_DEF=9600,8,1,0,2) and run at
;     the rate UART_BIT_DELAY selects. Without flow control, whatever arrives
;     outside a read, or after its second byte, is lost.
;   - There is no status to sample, so the driver cannot tell a missing ESP
;     from a quiet one: uart_write always reports success.
;   - A received byte whose stop bit reads low is dropped and latches
;     UART_RX_FRAMING in uart_rx_errors, as a framing error does on the Next.
;     From then on uart_read and uart_read_block report nothing until
;     uart_flush (or uart_init): a stream with a hole is never handed over
;     as whole, and a line held low no longer reads as a run of 0x00 bytes.
;
;   uart_init        Port A as output, TX and CTS high, forget a kept byte,
;                    clear uart_rx_errors. Leaves port A (R14) selected.
;                    It does not wait and sends nothing. It writes the whole
;                    mixer as UART_MIXER_VALUE. Destroys A, BC.
;   uart_init_keep_mixer  As uart_init, but it only sets mixer bit 6 (port
;                    A as output): tone and noise stay as they were. A music
;                    player that rewrites register 7 must keep bit 6 set, or
;                    the TX and CTS lines stop driving the ESP.
;   uart_read        CF=1 and A = byte, or CF=0 when no start bit came within
;                    UART_START_POLLS samples with CTS low, nor within
;                    UART_LATE_POLLS more after raising it, where a byte the
;                    ESP had already started would show (3.9 ms in all).
;                    CF=0 at once, without touching the port, while
;                    uart_rx_errors is not zero.
;                    Destroys BC and the alternate set (AF', BC', DE', HL').
;                    Preserves DE, HL. A second byte that follows the first
;                    at once is kept and returned by the next call without
;                    touching the port; one with a bad stop bit is dropped
;                    and latched, and the first is still returned.
;   uart_read_raw    As uart_read without the uart_rx_errors gate. The read
;                    that finds a bad stop bit still reports no byte.
;   uart_ready       CF=1 when a kept byte is waiting and no error is latched.
;                    CF=0 says nothing about the ESP: poll with uart_read.
;                    Destroys A.
;   uart_write       A = byte. Always CF=0. Preserves A, BC, DE, HL.
;   uart_flush       Clear uart_rx_errors, then read and discard, bytes with
;                    a bad stop bit included, until a read times out: CF=0.
;                    CF=1 when UART_FLUSH_MAX bytes went by and more keep
;                    coming: UART_RX_RECOVERY stays latched until a later
;                    flush completes or uart_init. Normal reads stay refused.
;                    Destroys A, BC and the alternate set.
;   uart_read_block  HL = buffer, B = room (1..255, 0 = 256). Stores bytes
;                    until the buffer is full or a read reports nothing (a
;                    timeout, a bad stop bit, errors latched). Out: HL =
;                    next free position, D = room left, CF=1 buffer full,
;                    CF=0 otherwise. Destroys A, BC, E and the alternate set.

UART_AY_REGISTER    EQU 0xFFFD  ; register select; reads the selected register
UART_AY_DATA        EQU 0xBFFD
UART_AY_MIXER       EQU 0x07
UART_AY_PORT_A      EQU 0x0E
; Port A as output (bit 6). The other bits are what every application has
; always written: noise off, tone C off.
UART_MIXER_VALUE    EQU 0xFC
UART_LINE_IDLE      EQU 0xFE    ; TX high, CTS high: the ESP holds its data
UART_LINE_LISTEN    EQU 0xFA    ; TX high, CTS low: the ESP may send
; Delay count per bit: 11 = 9600 baud, the only rate the tests check. 25
; gives 4800 and 54 gives 2400, but the late window after a timeout
; (UART_LATE_POLLS) is 1.5 bit times only at 9600: measure before using them.
UART_BIT_DELAY      EQU 11
UART_START_POLLS    EQU 240     ; 54 T-states each, CTS low
UART_LATE_POLLS     EQU 13      ; 42 T-states each, CTS high: 1.5 bit times
UART_FLUSH_MAX      EQU 2048    ; over two seconds of data at 9600 baud
UART_RX_FRAMING     EQU 0x40    ; uart_rx_errors: a stop bit read low, as on the Next
UART_RX_RECOVERY    EQU 0x80    ; uart_rx_errors: flush stopped before a quiet read

uart_init:
    include "ay_init_ports.inc"
    ld a, UART_MIXER_VALUE
    include "ay_init_output.inc"

uart_write:
    di                          ; a byte is timed as a whole
    push hl
    push de
    push bc
    push af
    ld bc, UART_AY_REGISTER
    ld de, 11 * 256 + UART_AY_PORT_A ; D = 1 start, 8 data, 2 stop bits
    out (c), e
    ld b, UART_AY_DATA / 256    ; C is the same in both ports
    cpl
    ld e, a                     ; the byte inverted: CF=1 sends a low level
    scf                         ; the start bit

; 370 T-states per bit with UART_BIT_DELAY 11, whatever the bit: both levels
; take the same path. Do not edit without measuring.
uart_write_bit:
    sbc a, a                    ; CF=1: 0xFF, CF=0: 0x00
    cpl
    or 0xF7
    dec a                       ; 0xF6 = TX low, 0xFE = TX high; CTS stays high
    out (c), a
    ld hl, UART_BIT_DELAY + 1
uart_write_next:
    dec hl
    ld a, h
    or l
    jr nz, uart_write_next
    srl e                       ; the next bit; the zeros that follow the
    dec d                       ; byte are the stop bits
    jp nz, uart_write_bit       ; JP: 10 T-states taken or not

    pop af
    pop bc
    pop de
    pop hl
    or a                        ; CF=0
    ei
    ret

uart_read:
    ld a, (uart_rx_errors)
    or a
    ret nz                      ; CF=0: no byte from a damaged stream
uart_read_raw:
    ld a, (uart_second_avail)
    dec a                       ; it holds 1 while a byte is kept, 0 otherwise
    jr nz, uart_read_start
    ld (uart_second_avail), a
    ld a, (uart_second_byte)
    scf
    ret
; The delays, the poll count and the byte being received live in the main
; set; the port constants in the alternate one, where the wait for a start
; bit runs. EXX switches between them.
uart_read_start:
    push hl
    push de
    di
    ld de, UART_BIT_DELAY
    ; To the middle of the first data bit, 1.5 bit times after the start edge.
    ld hl, UART_BIT_DELAY / 2 + UART_BIT_DELAY + 1
    ld b, UART_START_POLLS
    exx
    ld bc, UART_AY_REGISTER
    ld de, 0xFFBF               ; D and E = high bytes of the two ports
    ld hl, UART_LINE_IDLE * 256 + UART_LATE_POLLS
    ld a, UART_AY_PORT_A
    out (c), a
    ld b, e
    ld a, UART_LINE_LISTEN
    out (c), a                  ; CTS low

; From here to the last port access the code is timed: every IN and OUT falls
; on a fixed T-state after the previous one, and the instructions marked as
; delay only pass time. Do not edit without measuring (tests/test_ay.py
; sweeps the bit period it accepts).
uart_read_wait_start:
    ld b, d
    in a, (c)
    and 0x80
    jr z, uart_read_start_found
uart_read_timeout:
    exx
    dec b
    exx
    jr nz, uart_read_wait_start
    ld b, e
    out (c), h                  ; CTS high
; A byte the ESP started just before it saw CTS rise is still coming.
    ld b, d
uart_read_late:
    in a, (c)
    and 0x80
    jr z, uart_read_late_found
uart_read_late_next:
    dec l
    jr nz, uart_read_late
uart_read_none:
    exx
    pop de
    pop hl
    xor a                       ; CF=0
    ei
    ret
uart_read_late_found:
    in a, (c)
    and 0x80
    jr nz, uart_read_late_next
    in a, (c)
    and 0x80
    jr nz, uart_read_late_next
    exx
    ld hl, UART_BIT_DELAY / 2 + UART_BIT_DELAY ; one delay step less: this path is slower
    jr uart_read_first_bit
uart_read_start_found:
    in a, (c)
    and 0x80
    jr nz, uart_read_timeout

    in a, (c)
    and 0x80
    jr nz, uart_read_timeout
uart_read_start_bit:
    ; Start bit found!
    exx
uart_read_first_bit:
    ld b, 0x80                  ; the byte gathers in B; its eighth shift
    jr uart_read_delay          ; carries this bit out
uart_read_tune:
    add hl, de
    inc hl                      ; HL = UART_BIT_DELAY + 1
    ld a, 0                     ; delay
uart_read_delay:
    dec hl
    ld a, h
    or l
    jr nz, uart_read_delay

    cpl                         ; A = 0xFF: IN A,(n) reads port 0xFFFD
    in a, (UART_AY_REGISTER & 0xFF)
    rla                         ; the line -> CF
    rr b
    jr nc, uart_read_tune
uart_read_byte:
    ld a, b
    scf
    push af                     ; the byte, CF=1
    dec hl
    dec hl                      ; HL = -2. INC BC is delay
    inc bc
    exx
    ld a, UART_LINE_IDLE
    ld b, e
    out (c), a                  ; CTS high

; The ESP may already have started another byte: wait for it.
    exx
    ld bc, UART_AY_REGISTER
    add hl, de                  ; UART_BIT_DELAY - 2: to the middle of the stop bit
uart_read_stop_delay:
    dec hl
    ld a, h
    or l
    jr nz, uart_read_stop_delay

    in a, (c)                   ; the stop bit
    and 0x80
    jr z, uart_read_bad_stop
    add hl, de
    add hl, de
    add hl, de

uart_read_wait_second:
    in a, (c)
    and 0x80
    jr z, uart_read_second_found
uart_read_second_next:
    dec hl
    ld a, h
    or l
    jr nz, uart_read_wait_second

    ; No second byte
uart_read_done:
    pop af
    pop de
    pop hl
    ei
    ret

uart_read_bad_stop:
    pop af                      ; the byte is not whole: dropped. The exit
    xor a                       ; takes A = 0 and CF=0 from the stack instead
    push af
uart_read_second_bad:           ; (the first byte is whole and is returned)
    ld hl, uart_rx_errors
    set 6, (hl)                 ; keep recovery pending after an incomplete flush
    jr uart_read_done

uart_read_second_found:
    in a, (c)
    and 0x80
    jr nz, uart_read_second_next ; a false start still uses up a poll
    ld b, 0x80
    add hl, de                  ; delay, with the INC
    inc hl
    ld hl, UART_BIT_DELAY / 2 + UART_BIT_DELAY
    jr uart_read_second_delay
uart_read_second_tune:
    ld hl, UART_BIT_DELAY + 1
    nop                         ; delay

uart_read_second_delay:
    dec hl
    ld a, h
    or l
    jr nz, uart_read_second_delay

    cpl
    in a, (UART_AY_REGISTER & 0xFF)
    rla
    ; After a zero bit the next sample comes 5 T-states later than after a
    ; one, as it always has: the taken JR is that difference.
    jr nc, uart_read_second_zero
uart_read_second_zero:
    rr b
    jr nc, uart_read_second_tune
uart_read_second_done:
    add hl, de                  ; UART_BIT_DELAY: to the middle of the stop bit
    ret c                       ; delay: the ADD left CF=0, neither RET returns
    ret c
uart_read_second_stop:
    dec hl
    ld a, h
    or l
    jr nz, uart_read_second_stop
    cpl
    in a, (UART_AY_REGISTER & 0xFF)
    rla
    jr nc, uart_read_second_bad
    ld hl, uart_second_avail
    inc (hl)                    ; 0 -> 1: the read began with it clear
    inc hl
    ld (hl), b                  ; uart_second_byte
    jr uart_read_done

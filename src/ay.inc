; AY bit-banged UART driver for the ESP: serial on I/O port A of the
; AY-3-8912. Needs ay_vars.inc; ay_speed.inc is optional.
;
; The timed bit loops descend, through NetManZX, from netman-zx by Alex
; Nihirash (github.com/nihirash/netman-zx).
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
;
;   uart_init        Port A as output, TX and CTS high, forget a kept byte.
;                    It does not wait and sends nothing. Destroys A, BC.
;   uart_read        CF=1 and A = byte, or CF=0 when no start bit came within
;                    UART_START_POLLS samples with CTS low, nor within
;                    UART_LATE_POLLS more after raising it, where a byte the
;                    ESP had already started would show (3.9 ms in all).
;                    Destroys BC and the alternate set (AF', BC', DE', HL').
;                    Preserves DE, HL. A second byte that follows the first
;                    at once is kept and returned by the next call without
;                    touching the port.
;   uart_read_raw    The same routine: there are no receive errors to latch.
;   uart_ready       CF=1 when a kept byte is waiting. CF=0 says nothing
;                    about the ESP: poll with uart_read. Destroys A.
;   uart_write       A = byte. Always CF=0. Preserves A, BC, DE, HL.
;   uart_flush       Read and discard until a read times out: CF=0. CF=1 when
;                    UART_FLUSH_MAX bytes went by and more keep coming.
;                    Destroys A, BC and the alternate set.
;   uart_read_block  HL = buffer, B = room (1..255, 0 = 256). Stores bytes
;                    until the buffer is full or a read times out. Out: HL =
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
; Delay count per bit: 54 = 2400, 25 = 4800, 11 = 9600 baud.
UART_BIT_DELAY      EQU 11
UART_START_POLLS    EQU 240     ; 54 T-states each, CTS low
UART_LATE_POLLS     EQU 13      ; 42 T-states each, CTS high: 1.5 bit times
UART_FLUSH_MAX      EQU 2048    ; over two seconds of data at 9600 baud

uart_init:
    xor a
    ld (uart_second_avail), a
    di                          ; a handler must not split select and data
    ld bc, UART_AY_REGISTER
    ld a, UART_AY_MIXER
    out (c), a
    ld b, 0xBF                  ; UART_AY_DATA
    ld a, UART_MIXER_VALUE
    out (c), a
    ld b, 0xFF
    ld a, UART_AY_PORT_A
    out (c), a
    ld b, 0xBF
    ld a, UART_LINE_IDLE        ; a known level, whatever the latch held
    out (c), a
    ei
    ret

uart_ready:
    ld a, (uart_second_avail)
    add a, 0xFF                 ; CF=1 when not zero
    ret

uart_write:
    di                          ; a byte is timed as a whole
    push hl
    push de
    push bc
    push af
    ld l, a
    ld bc, UART_AY_REGISTER
    ld a, UART_AY_PORT_A
    out (c), a
    ld de, UART_BIT_DELAY - 2
    ld a, l
    cpl
    scf
    ld b, 11                    ; 1 start, 8 data, 2 stop bits

; 370 T-states per bit with UART_BIT_DELAY 11. Do not edit without measuring.
uart_write_bit:
    push bc
    push af

    ld a, 0xfe
    ld h, d
    ld l, e
    ld bc, UART_AY_DATA
    jp nc, uart_write_one
; Transmit Zero:
    and 0xf7
    out (c), a
    jr uart_write_next
uart_write_one:
    or 8
    out (c), a
    jr uart_write_next

uart_write_next:
    dec hl
    ld a, h
    or l
    jr nz, uart_write_next

    nop
    nop
    nop

    pop af
    pop bc
    or a
    rra
    djnz uart_write_bit

    pop af
    pop bc
    pop de
    pop hl
    or a                        ; CF=0
    ei
    ret

uart_read:
uart_read_raw:
    push hl
    ld hl, uart_second_avail
    ld a, (hl)
    or a
    jr z, uart_read_start
    ld (hl), 0
    inc hl
    ld a, (hl)
    pop hl
    scf
    ret
uart_read_start:
    push de
    di
    exx
    ld de, UART_BIT_DELAY
    ld hl, UART_BIT_DELAY / 2
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

; From here to uart_read_byte the code is timed. Do not edit without
; measuring (tests/test_ay.py sweeps the bit period it accepts).
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
    dec l                       ; one delay step less: this path is slower
    exx
    jp uart_read_start_bit
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
    ld bc, UART_AY_REGISTER
    ld a, 0x80
    ex af, af'
uart_read_tune:
    add hl, de ; HL = 1.5 * UART_BIT_DELAY
    nop
    nop
    nop
    nop ; Fine tuning delay

uart_read_delay:
    dec hl
    ld a, h
    or l
    jr nz, uart_read_delay

    in a, (c)
    and 0x80
    jp z, uart_read_zero
; One received:
    ex af, af'
    scf
    rra
    jr c, uart_read_byte
    ex af, af'
    jp uart_read_tune
uart_read_zero:
    ex af, af'
    or a
    rra
    jr c, uart_read_byte
    ex af, af'
    jp uart_read_tune
uart_read_byte:
    scf
    push af                     ; the byte, CF=1
    exx
    ld a, UART_LINE_IDLE
    ld b, e
    out (c), a                  ; CTS high

; The ESP may already have started another byte: wait for it.
    exx
    ld h, d
    ld l, e

    ld bc, 0x0007
    or a
    sbc hl, bc

uart_read_stop_delay:
    dec hl
    ld a, h
    or l
    jr nz, uart_read_stop_delay

    ld bc, UART_AY_REGISTER
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
    exx
    pop af
    pop de
    pop hl
    ei
    ret

uart_read_second_found:
    in a, (c)
    and 0x80
    jr nz, uart_read_second_next ; a false start still uses up a poll
    ld h, d
    ld l, e
    ld bc, 0x0002
    srl h
    rr l
    or a
    sbc hl, bc
    ld bc, UART_AY_REGISTER
    ld a, 0x80
    ex af, af'
uart_read_second_tune:
    nop
    nop
    nop
    nop
    add hl, de

uart_read_second_delay:
    dec hl
    ld a, h
    or l
    jr nz, uart_read_second_delay

    in a, (c)
    and 0x80
    jr z, uart_read_second_zero
; Second 1 received
    ex af, af'
    scf
    rra
    jr c, uart_read_second_done
    ex af, af'
    jp uart_read_second_tune

uart_read_second_zero:
    ex af, af'
    or a
    rra
    jr c, uart_read_second_done
    ex af, af'
    jp uart_read_second_tune
uart_read_second_done:
    ld hl, uart_second_avail
    ld (hl), 1
    inc hl
    ld (hl), a                  ; uart_second_byte
    jr uart_read_done

uart_flush:
    ld bc, UART_FLUSH_MAX + 1   ; the extra read tells whether more is coming
uart_flush_next:
    push bc
    call uart_read
    pop bc
    ret nc                      ; the line went quiet
    dec bc
    ld a, b
    or c
    jr nz, uart_flush_next
    ld (uart_second_avail), a   ; gave up: keep no byte from before
    scf
    ret

uart_read_block:
    ld d, b                     ; D = room
uart_read_block_next:
    call uart_read
    ret nc
    ld (hl), a
    inc hl
    dec d
    jr nz, uart_read_block_next
    scf
    ret

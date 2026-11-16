; Send helpers shared by every backend. Include after the backend: they only
; call uart_write (A = byte, CF=1 when not sent, preserves BC and HL).

; HL = NUL-terminated string. CF=0 all sent, HL at the terminator. CF=1 a
; byte was not sent, HL at that byte. Destroys A.
uart_write_string:
    ld a, (hl)
    or a
    ret z
    call uart_write
    ret c
    inc hl
    jr uart_write_string

; HL = data, BC = length. CF=0 all sent. CF=1 a byte was not sent, HL at that
; byte and BC = bytes left. Destroys A.
; On the Next nothing is read while it sends: the 512-byte RX FIFO takes what
; arrives meanwhile, and a byte that does not fit is lost (the overflow still
; latches). With both FIFOs empty and the ESP sending at the same rate, input
; is lost past 575 bytes at 115200 baud, and sooner when the line outruns
; this loop (about 218 T-states a byte): past 329 bytes at 2000000 baud and
; 28 MHz. Split long sends and drain input between the parts. On the Next,
; uart_write_block_fast (next_write_block.inc) keeps up with that line in
; the model, which leaves out the Next's wait states: 573 bytes there. The divMMC and AY ports hold the ESP back with CTS instead.
uart_write_block:
    ld a, b
    or c
    ret z
    ld a, (hl)
    call uart_write
    ret c
    inc hl
    dec bc
    jr uart_write_block

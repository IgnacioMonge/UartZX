; Optional block send; requires uart_write from any core. No extra state.

; HL = data, BC = length. CF=0 all sent. CF=1 a byte was not sent, HL at that
; byte and BC = bytes left. Destroys A.
; On the Next nothing is read while it sends: the 512-byte RX FIFO takes what
; arrives meanwhile, and a byte that does not fit is lost (the overflow still
; latches). With both FIFOs empty and the ESP sending at the same rate, input
; is lost past 575 bytes at 115200 baud, and sooner when the line outruns
; this loop (about 183 T-states a byte): past 391 bytes at 2000000 baud and
; 28 MHz. Split long sends and drain input between the parts. On the Next,
; uart_write_block_fast (next_write_block.inc) keeps up with that line in
; the model, which leaves out the Next's wait states: 574 bytes there. The divMMC and AY ports hold the ESP back with CTS instead.
uart_write_block:
    ld a, b
    or c
    ret z
uart_write_block_byte:
    ld a, (hl)
    call uart_write
    ret c
    cpi                         ; advance HL, decrement BC; CF stays clear
    jp pe, uart_write_block_byte ; P/V means bytes remain
    ret

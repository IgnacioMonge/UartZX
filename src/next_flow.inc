; Optional ESP hardware flow controls; include after next_core.inc or next.inc.
; uart_flow_enable / uart_flow_disable set / clear only frame bit 5 on ESP.
; Restore the previous ESP/Pi selection without rewriting either baud divisor.
; No status/data reads, FIFO reset, AT command, wait, or extra state.
; Requires an initialized UART with frame reset clear. Enable only after the
; caller checks board/pin support and negotiates RTS+CTS with the ESP.
; Init still selects 8N1 with flow off. No runtime capability probe or result.
; Preserves HL, IX/IY, alternate registers and IFF; destroys AF, BC, DE.
; Maskable interrupts cannot split selection/read-modify-write/restore. NMI
; handlers must not access the UART registers during this sequence.

uart_flow_enable:
    ld d, 0x20
    defb 0x01                   ; LD BC,nn: its operand hides the next LD
uart_flow_disable:
    ld d, 0
uart_flow_update:
    ld a, i
    push af
    di
    ld bc, UART_SELECT
    in e, (c)                   ; readback has divisor-write enable clear
    xor a
    out (c), a                  ; ESP, without latching prescaler bits
    inc b                       ; UART_FRAME
    in a, (c)
    and 0xDF
    or d
    out (c), a
    dec b                       ; UART_SELECT
    out (c), e
    pop af
    ret po
    ei
    ret

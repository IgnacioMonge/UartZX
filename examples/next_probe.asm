; Unattended hardware probe. PC connects to ESP TCP port 6145 for telemetry.
; Uses only volatile UART_CUR settings; no Wi-Fi credentials or SD writes.
; Reports and phase protocol: tools/next_probe.py. sjasmplus builds the NEX.
    device ZXSPECTRUMNEXT
    org 0x8000
    IFDEF PROBE_BATCH
    UNDEFINE PROBE_BATCH
    DEFINE PROBE_BATTERY
    DEFINE PROBE_DUPLEX
    ENDIF
    IFDEF PROBE_SOAK
    UNDEFINE PROBE_SOAK           ; CLI defines are textual: avoid matching _FRAMES
    DEFINE PROBE_DUPLEX
    ENDIF
    IFNDEF PROBE_TOKEN
PROBE_TOKEN EQU 0
    ENDIF

probe_entry:
    di
    ld sp, 0xBFF0
    call probe_run
    ld a, 4
    jr nc, probe_idle
    ld a, 2
probe_idle:
    out (0xFE), a
    ei
probe_halt:
    halt
    jr probe_halt

probe_run:
    ld a, 0xB0
    ld i, a
    im 2
    ei
    ld a, 7
    call probe_nextreg_read
    ld (probe_old_cpu), a
    IFDEF PROBE_BATTERY
    ld a, 5
    call probe_nextreg_read
    ld (probe_batch_old_video), a
    ENDIF
    ld d, 3
    ld a, 7
    call probe_nextreg_write
    ld a, 1
    out (0xFE), a
    ld a, 0x0F
    call probe_nextreg_read
    ld (probe_board), a
    ld a, 0x11
    call probe_nextreg_read
    and 7
    ld (probe_timing), a
    ld a, 1
    call probe_nextreg_read
    ld (probe_core), a
    ld a, 0x0E
    call probe_nextreg_read
    ld (probe_subcore), a
    call uart_init
    ld hl, probe_scan_tables
    ld b, 5
probe_scan:
    push bc
    push hl
    ld e, (hl)
    inc hl
    ld d, (hl)
    ex de, hl
    call uart_set_baud
    ld hl, probe_at
    call probe_command
    pop hl
    pop bc
    jr nc, probe_found
    inc hl
    inc hl
    djnz probe_scan
    jp probe_abort
probe_found:
    xor a
    ld (probe_rate), a
    ld (probe_flow), a
    call probe_set_rate
    jp c, probe_abort
    ld hl, probe_echo_off
    call probe_command
    jp c, probe_abort
    ld hl, probe_server_off
    call probe_command           ; an absent inherited listener may say ERROR
    ld hl, probe_mux
    call probe_command
    jp c, probe_abort
    ld hl, probe_active_rx       ; BridgeZX may leave passive receive enabled
    call probe_command
    jp c, probe_abort
    ld hl, probe_ipd_info
    call probe_command
    jp c, probe_abort
    ld hl, probe_server_on
    call probe_command
    jp c, probe_abort
    call probe_deadline
probe_connect_wait:
    ld a, (probe_connected_seen)
    or a
    jr nz, probe_client_ready
    call probe_line
    jp c, probe_abort
    ld hl, probe_line_buffer
    ld de, probe_connected
    call probe_equal
    jr nz, probe_connect_wait
probe_client_ready:
    call probe_metrics_reset
    xor a                       ; event 0: capabilities before any flow changes
    call probe_emit
    jp c, probe_abort
    IFDEF PROBE_BATTERY
    call probe_batch_run
    jp c, probe_abort
    jp probe_complete
    ENDIF
    ld hl, probe_plan
    ld (probe_plan_pointer), hl
probe_plan_next:
    ld hl, (probe_plan_pointer)
    ld a, (hl)
    cp 0xFF
    jp z, probe_complete
    ld (probe_rate), a
    inc hl
    ld a, (hl)
    ld (probe_flow), a
    inc hl
    ld (probe_plan_pointer), hl
    call probe_metrics_reset
    ld a, (probe_flow)
    or a
    jr z, probe_plan_configure
    ld a, (probe_board)
    and 0x0F
    cp 2
    jr z, probe_plan_configure
    cp 3
    jr z, probe_plan_configure
    ld a, 4                     ; known capable raw IDs only; unknown means no
    ld (probe_result), a
    ld a, 8
    call probe_emit
    jp c, probe_abort
    jr probe_plan_next
probe_plan_configure:
    ld a, 5                     ; event 5 announces the requested transition
    call probe_emit
    jp c, probe_abort
    call probe_set_rate
    jr nc, probe_plan_ready
    ld a, 5
    ld (probe_result), a
    call probe_recover
    jp c, probe_abort
    ld a, 5
    call probe_emit
    jp c, probe_abort
    jr probe_plan_next
probe_plan_ready:
    IFDEF PROBE_DUPLEX
    call probe_soak_phase
    jp c, probe_abort
    ELSE
    call probe_rx_test
    jp c, probe_abort
    call probe_tx_test
    jp c, probe_abort
    ENDIF
    jp probe_plan_next
probe_complete:
    call probe_recover
    jp c, probe_abort
    call probe_metrics_reset
    ld hl, probe_server_off      ; stop listening while keeping the telemetry connection
    call probe_command
    jr c, probe_server_stop_failed
    ld a, 6
    call probe_emit
    jp nc, probe_restore        ; the collector closes its socket after DONE
    jr probe_final_report_failed
probe_server_stop_failed:
    ld a, 6                     ; fatal result: server shutdown failed
    ld (probe_result), a
    ld a, 7
    call probe_emit
probe_final_report_failed:
    ld hl, probe_close
    call probe_command
    call probe_restore
    scf
    ret
probe_abort:
    IFDEF PROBE_BATTERY
    call probe_recover          ; bounded best effort to leave UART_CUR reusable
    ENDIF
    call uart_flow_disable
    call probe_restore
    ld a, 2
    out (0xFE), a
    scf
    ret
probe_restore:
    IFDEF PROBE_BATTERY
    ld a, (probe_batch_old_video)
    ld d, a
    ld a, 5
    call probe_nextreg_write
    ENDIF
    ld a, (probe_old_cpu)
    and 3                       ; read-only actual-speed bits are never written
    ld d, a
    ld a, 7
    jp probe_nextreg_write

probe_nextreg_read:
    ld bc, NEXTREG_SELECT
    out (c), a
    inc b
    in a, (c)
    ret
probe_nextreg_write:
    ld bc, NEXTREG_SELECT
    out (c), a
    inc b
    out (c), d
    ret

; One global command/chunk deadline: quick/soak 150 frames, batch 500.
probe_deadline:
    IFDEF PROBE_BATTERY
    push hl
    ld hl, (probe_ticks)
    ld (probe_started), hl
    pop hl
    ELSE
    ld a, (probe_ticks)
    ld (probe_started), a
    ENDIF
    ret
probe_expired:
    push hl
    IFDEF PROBE_BATTERY
    push de
    ld hl, (probe_ticks)
    ld de, (probe_started)
    or a
    sbc hl, de
    ld de, 500
    or a
    sbc hl, de
    pop de
    ELSE
    ld hl, probe_started
    ld a, (probe_ticks)
    sub (hl)
    cp 150
    ENDIF
    pop hl
    ret
probe_get:
    call probe_expired
    ret nc
    call uart_read_raw
    ret c
    jr probe_get
probe_put:
    push de
    push hl
    ld e, a
probe_put_retry:
    ld a, e
    call uart_try_write
    jr nc, probe_put_done
    ld a, (probe_track_busy)
    or a
    jr z, probe_put_service
    ld hl, (probe_busy)
    inc hl
    ld (probe_busy), hl
probe_put_service:
    call uart_read_raw           ; TX phases have no incoming test payload
    call probe_expired
    jr c, probe_put_retry
    scf
probe_put_done:
    pop hl
    pop de
    ret
probe_string:
    ld a, (hl)
    or a
    ret z
    call probe_put
    ret c
    inc hl
    jr probe_string
probe_line:
    ld hl, probe_line_buffer
    ld d, 95
    ld e, 0
probe_line_next:
    call probe_get
    jr nc, probe_line_failed
    cp 13
    jr z, probe_line_next
    cp 10
    jr z, probe_line_end
    ld (hl), a
    inc hl
    inc e
    dec d
    jr nz, probe_line_next
probe_line_failed:
    scf
    ret
probe_line_end:
    ld a, e
    or a
    jr z, probe_line_next
    xor a
    ld (hl), a
    ret
probe_equal:
    ld a, (de)
    cp (hl)
    ret nz
    inc hl
    inc de
    or a
    jr nz, probe_equal
    ret
probe_wait_ok:
    call probe_line
    ret c
    ld hl, probe_line_buffer
    ld de, probe_ok
    call probe_equal
    ret z
    ld hl, probe_line_buffer
    ld de, probe_connected
    call probe_equal
    jr nz, probe_wait_error
    ld a, 1
    ld (probe_connected_seen), a
    jr probe_wait_ok
probe_wait_error:
    ld hl, probe_line_buffer
    ld de, probe_send_ok
    call probe_equal
    ret z
    ld hl, probe_line_buffer
    ld de, probe_error
    call probe_equal
    jr z, probe_wait_failed
    jr probe_wait_ok
probe_wait_failed:
    scf
    ret
probe_command:
    call probe_deadline
    call probe_string
    ret c
    jp probe_wait_ok
probe_prompt:
    call probe_get
    jr nc, probe_wait_failed
    cp '>'
    jr nz, probe_prompt
    or a
    ret

; Build a volatile fast rate from the same kHz metadata the library exposes.
probe_set_rate:
    call uart_flow_disable
    ld a, (probe_rate)
    or a
    ld hl, probe_uart_default
    ld de, uart_baud_115200
    jr z, probe_set_selected
    ld hl, uart_baud_khz_2000000
    ld a, (probe_timing)
    add a, a
    ld e, a
    ld d, 0
    add hl, de
    ld e, (hl)
    inc hl
    ld d, (hl)
    ex de, hl
    ld de, probe_uart_fast_digits
    call probe_decimal4
    ld hl, probe_uart_fast
    ld de, uart_baud_2000000
probe_set_selected:
    ld (probe_divisor_table), de
    ld a, (probe_flow)
    or a
    ld a, '0'
    jr z, probe_set_flow_digit
    ld a, '3'
probe_set_flow_digit:
    ld (probe_uart_default_flow), a
    ld (probe_uart_fast_flow), a
    call probe_deadline
    call probe_string
    ret c
    ld b, 10
probe_set_settle:
    halt
    djnz probe_set_settle
    ld hl, (probe_divisor_table)
    call uart_set_baud
    xor a
    ld (uart_tx_failed), a
    ld (uart_rx_errors), a
    ld de, 4096
    call uart_flush_budget
    ld hl, probe_at
    call probe_command
    ret c                       ; do not arm FPGA if ESP did not confirm
    ld a, (probe_flow)
    or a
    ret z
    call uart_flow_enable
    ld hl, probe_at
    jp probe_command
probe_recover:
    xor a
    ld (probe_rate), a
    ld (probe_flow), a
    jp probe_set_rate
probe_decimal4:
    ld bc, 1000
    call probe_decimal_digit
    ld bc, 100
    call probe_decimal_digit
    ld bc, 10
    call probe_decimal_digit
    ld a, l
    add a, '0'
    ld (de), a
    ret
probe_decimal_digit:
    ld a, '0'-1
probe_decimal_sub:
    inc a
    or a
    sbc hl, bc
    jr nc, probe_decimal_sub
    add hl, bc
    ld (de), a
    inc de
    ret

probe_metrics_reset:
    xor a
    ld (probe_result), a
    ld (probe_errors), a
    ld (uart_rx_errors), a
    ld hl, probe_count
    ld de, probe_count+1
    ld bc, 11
    ld (hl), a
    ldir
    ld a, 1
    ld (probe_pattern), a
    ret
probe_metrics_finish:
    ld a, (uart_rx_errors)
    ld (probe_errors), a
    IFDEF PROBE_BATTERY
    ld hl, (probe_ticks)
    ld de, (probe_started)
    or a
    sbc hl, de
    ld (probe_duration), hl
    ELSE
    ld hl, probe_started
    ld a, (probe_ticks)
    sub (hl)
    ld (probe_duration), a
    ENDIF
    ret
probe_pattern_next:
    ld a, (probe_pattern)
    srl a
    jr nc, probe_pattern_store
    xor 0xB8
probe_pattern_store:
    ld (probe_pattern), a
    ret
probe_count_byte:
    ld e, a
    ld d, 0
    ld hl, (probe_sum)
    add hl, de
    ld (probe_sum), hl
    ld hl, (probe_count)
    inc hl
    ld (probe_count), hl
    ret

probe_emit:
    call probe_emit_send
    ret c
    jp probe_wait_ok
probe_emit_send:
    ld (probe_event), a
    ld bc, UART_FRAME
    in a, (c)
    ld (probe_frame), a
    ld hl, probe_send_report
    call probe_deadline
    call probe_string
    ret c
    call probe_prompt
    ret c
    ld hl, probe_report
    ld b, 32
probe_emit_bytes:
    push bc
    ld a, (hl)
    call probe_put
    pop bc
    ret c
    inc hl
    djnz probe_emit_bytes
    or a
    ret

probe_rx_test:
    call probe_metrics_reset
    call probe_step_next
    ld hl, 4096
    ld (probe_count), hl
    ld a, 1
    call probe_emit_send         ; SEND OK is skipped by IPD search, not awaited
    ret c
    call probe_metrics_reset
    call probe_deadline
    IFDEF PROBE_BATTERY
    call probe_batch_pause
    ELSE
    ld b, 10                    ; stall RX for about 200ms, both flow modes
probe_rx_pause:
    halt
    djnz probe_rx_pause
    ENDIF
probe_rx_header:
    call probe_get
    jp nc, probe_rx_timeout
    cp '+'
    jr nz, probe_rx_header
    ld hl, probe_ipd_prefix
probe_rx_prefix:
    call probe_get
    jp nc, probe_rx_timeout
    cp (hl)
    jr nz, probe_rx_header
    inc hl
    ld a, (hl)
    or a
    jr nz, probe_rx_prefix
    ld hl, 0
    ld d, 0
probe_rx_length:
    call probe_get
    jp nc, probe_rx_timeout
    cp ':'
    jr z, probe_rx_body_start
    sub '0'
    cp 10
    jp nc, probe_rx_timeout
    ld e, a
    inc d
    ld a, d
    cp 5
    jp nc, probe_rx_timeout
    push de
    ld d, h
    ld e, l
    add hl, hl
    add hl, hl
    add hl, de
    add hl, hl
    pop de
    ld b, 0
    ld c, e
    add hl, bc
    jr probe_rx_length
probe_rx_body_start:
    ld a, h
    or l
    jp z, probe_rx_timeout
    ld a, h
    cp 0x11
    jp nc, probe_rx_timeout
    ld (probe_packet_left), hl
probe_rx_body:
    call probe_get
    jp nc, probe_rx_timeout
    ld e, a
    ld a, (probe_pattern)
    cp e
    jr z, probe_rx_match
    ld hl, (probe_bad)
    inc hl
    ld (probe_bad), hl
probe_rx_match:
    ld a, e
    call probe_count_byte
    call probe_pattern_next
    ld hl, (probe_count)
    ld a, h
    cp 16
    jr z, probe_rx_done
    ld hl, (probe_packet_left)
    dec hl
    ld (probe_packet_left), hl
    ld a, h
    or l
    jr nz, probe_rx_body
    jp probe_rx_header
probe_rx_timeout:
    ld a, 1
    ld (probe_result), a
probe_rx_done:
    call probe_metrics_finish
    ld a, (probe_result)
    or a
    jr nz, probe_rx_report
    ld hl, (probe_bad)
    ld a, h
    or l
    ld a, 2
    jr nz, probe_rx_set_result
    ld a, (probe_errors)
    or a
    jr z, probe_rx_report
    ld a, 3
probe_rx_set_result:
    ld (probe_result), a
probe_rx_report:
    IFDEF PROBE_BATTERY
    jp probe_batch_rx_report
    ENDIF
    ; Finish/dismiss the bounded burst before issuing any further AT command.
    ld de, 32768
    call uart_flush_budget
    ld a, 2
    jp probe_emit

probe_tx_test:
    call probe_metrics_reset
    call probe_step_next
    ld hl, 2048
    ld (probe_count), hl
    ld a, 3
    call probe_emit
    ret c
    call probe_metrics_reset
    call probe_deadline
    ld hl, probe_send_payload
    call probe_string
    ret c
    call probe_prompt
    ret c
    ld a, 1
    ld (probe_track_busy), a
    ld bc, 2048
probe_tx_loop:
    push bc
    ld a, (probe_pattern)
    call probe_put
    jr c, probe_tx_failed
    ld a, (probe_pattern)
    call probe_count_byte
    call probe_pattern_next
    pop bc
    dec bc
    ld a, b
    or c
    jr nz, probe_tx_loop
probe_tx_idle:
    call uart_tx_idle
    jr c, probe_tx_wire_done
    call probe_expired
    jr c, probe_tx_idle
    jr probe_tx_timeout
probe_tx_wire_done:
    call probe_wait_ok
    jr nc, probe_tx_done
    jr probe_tx_timeout
probe_tx_failed:
    pop bc
probe_tx_timeout:
    ld a, 1
    ld (probe_result), a
probe_tx_done:
    xor a
    ld (probe_track_busy), a
    call probe_metrics_finish
    ld a, 4
    jp probe_emit
probe_step_next:
    ld hl, (probe_step)
    inc hl
    ld (probe_step), hl
    ret

    include "../src/next_core.inc"
    include "../src/next_try_write.inc"
    include "../src/next_flush_budget.inc"
    include "../src/next_tx_idle.inc"
    include "../src/next_flow.inc"
    include "../src/next_baud_khz.inc"
    include "../src/next_baud_9600.inc"
    include "../src/next_baud_57600.inc"
    include "../src/next_baud_1152000.inc"
    include "../src/next_baud_2000000.inc"
    include "../src/next_vars.inc"

probe_at: db "AT",13,10,0
probe_echo_off: db "ATE0",13,10,0
probe_mux: db "AT+CIPMUX=1",13,10,0
probe_active_rx: db "AT+CIPRECVMODE=0",13,10,0
probe_ipd_info: db "AT+CIPDINFO=0",13,10,0
probe_server_on: db "AT+CIPSERVER=1,6145",13,10,0
probe_server_off: db "AT+CIPSERVER=0",13,10,0
probe_close: db "AT+CIPCLOSE=0",13,10,0
probe_uart_default: db "AT+UART_CUR=115200,8,1,0,"
probe_uart_default_flow: db '0',13,10,0
probe_uart_fast: db "AT+UART_CUR="
probe_uart_fast_digits: db "1929"
    db "000,8,1,0,"
probe_uart_fast_flow: db '0',13,10,0
probe_send_report: db "AT+CIPSEND=0,32",13,10,0
probe_send_payload: db "AT+CIPSEND=0,2048",13,10,0
probe_ipd_prefix: db "IPD,0,",0
probe_connected: db "0,CONNECT",0
probe_ok: db "OK",0
probe_send_ok: db "SEND OK",0
probe_error: db "ERROR",0
probe_scan_tables:
    dw uart_baud_2000000, uart_baud_1152000, uart_baud_115200
    dw uart_baud_57600, uart_baud_9600
    IFDEF PROBE_DUPLEX
probe_plan: db 0,1, 2,1, 0xFF
    ELSE
probe_plan: db 0,0, 0,1, 2,0, 2,1, 0xFF
    ENDIF
probe_plan_pointer: dw probe_plan
probe_divisor_table: dw uart_baud_115200
probe_old_cpu: db 0
probe_connected_seen: db 0
probe_track_busy: db 0
probe_ticks: dw 0
probe_started: dw 0
probe_pattern: db 1
probe_packet_left: dw 0
probe_line_buffer: ds 96,0

probe_report:
    db "UXP1"
probe_event: db 0
probe_rate: db 0
probe_flow: db 0
probe_result: db 0
probe_board: db 0
probe_timing: db 0
probe_core: db 0
probe_subcore: db 0
probe_errors: db 0
probe_frame: db 0
probe_step: dw 0
probe_count: dw 0
probe_sum: dw 0
probe_bad: dw 0
probe_busy: dw 0
probe_duration: dw 0
probe_reserved: dw 0
    dd PROBE_TOKEN
    ASSERT $-probe_report == 32
    IFDEF PROBE_DUPLEX
    include "next_probe_soak.inc"
    ENDIF
    IFDEF PROBE_BATTERY
    include "next_probe_batch.inc"
    ENDIF
    ASSERT $ < 0xA000

    ds 0xB000-$,0
    ds 257,0xB1
    ds 0xB1B1-$,0
probe_irq:
    push af
    push hl
    ld hl, (probe_ticks)
    inc hl
    ld (probe_ticks), hl
    pop hl
    pop af
    ei
    reti
    SAVENEX OPEN "UXPROBE.NEX", probe_entry, 0xBFF0
    SAVENEX CORE 3,0,0
    SAVENEX CFG 0
    SAVENEX BANK 2
    SAVENEX CLOSE
